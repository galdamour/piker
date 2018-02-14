"""
Questrade API backend.
"""
import inspect
import json
import time
import datetime

import trio
from async_generator import asynccontextmanager

from . import config
from ..log import get_logger, colorize_json

# TODO: move to urllib3/requests once supported
import asks
asks.init('trio')

log = get_logger('questrade')

_refresh_token_ep = 'https://login.questrade.com/oauth2/'
_version = 'v1'


class QuestradeError(Exception):
    "Non-200 OK response code"


def resproc(
    resp: asks.response_objects.Response,
    return_json: bool = True
) -> asks.response_objects.Response:
    """Process response and return its json content.

    Raise the appropriate error on non-200 OK responses.
    """
    if not resp.status_code == 200:
        raise QuestradeError(resp.body)

    try:
        data = resp.json()
    except json.decoder.JSONDecodeError:
        log.exception(f"Failed to process {resp}:\n{resp.text}")
        raise QuestradeError(resp.text)
    else:
        log.debug(f"Received json contents:\n{colorize_json(data)}")

    return data if return_json else resp


class Client:
    """API client suitable for use as a long running broker daemon or
    single api requests.

    Provides a high-level api which wraps the underlying endpoint calls.
    """
    def __init__(self, config: 'configparser.ConfigParser'):
        self._sess = asks.Session()
        self.api = _API(self._sess)
        self._conf = config
        self.access_data = {}
        self.user_data = {}
        self._apply_config(config)

    def _apply_config(self, config):
        self.access_data = dict(self._conf['questrade'])

    async def _new_auth_token(self) -> dict:
        """Request a new api authorization ``refresh_token``.

        Gain api access using either a user provided or existing token.
        See the instructions::

        http://www.questrade.com/api/documentation/getting-started
        http://www.questrade.com/api/documentation/security
        """
        resp = await self._sess.get(
            _refresh_token_ep + 'token',
            params={'grant_type': 'refresh_token',
                    'refresh_token': self.access_data['refresh_token']}
        )
        data = resproc(resp)
        self.access_data.update(data)

        return data

    def _prep_sess(self) -> None:
        """Fill http session with auth headers and a base url.
        """
        data = self.access_data
        # set access token header for the session
        self._sess.headers.update({
            'Authorization': (f"{data['token_type']} {data['access_token']}")})
        # set base API url (asks shorthand)
        self._sess.base_location = self.access_data['api_server'] + _version

    async def _revoke_auth_token(self) -> None:
        """Revoke api access for the current token.
        """
        token = self.access_data['refresh_token']
        log.debug(f"Revoking token {token}")
        resp = await asks.post(
            _refresh_token_ep + 'revoke',
            headers={'token': token}
        )
        return resp

    async def ensure_access(self, force_refresh: bool = False) -> dict:
        """Acquire new ``access_token`` and/or ``refresh_token`` if necessary.

        Checks if the locally cached (file system) ``access_token`` has expired
        (based on a ``expires_at`` time stamp stored in the brokers.ini config)
        expired (normally has a lifetime of 3 days). If ``false is set then
        and refreshs token if necessary using the ``refresh_token``. If the
        ``refresh_token`` has expired a new one needs to be provided by the
        user.
        """
        access_token = self.access_data.get('access_token')
        expires = float(self.access_data.get('expires_at', 0))
        expires_stamp = datetime.datetime.fromtimestamp(
            expires).strftime('%Y-%m-%d %H:%M:%S')
        if not access_token or (expires < time.time()) or force_refresh:
            log.info(f"Refreshing access token {access_token} which expired at"
                     f" {expires_stamp}")
            try:
                data = await self._new_auth_token()
            except QuestradeError as qterr:
                if "We're making some changes" in qterr.args[0]:
                    # API service is down
                    raise QuestradeError("API is down for maintenance")

                elif qterr.args[0].decode() == 'Bad Request':
                    # likely config ``refresh_token`` is expired
                    _token_from_user(self._conf)
                    self._apply_config(self._conf)
                    data = await self._new_auth_token()

            # store absolute token expiry time
            self.access_data['expires_at'] = time.time() + float(
                data['expires_in'])
            # write to config on disk
            write_conf(self)
        else:
            log.info(f"\nCurrent access token {access_token} expires at"
                     f" {expires_stamp}\n")

        self._prep_sess()
        return self.access_data

    async def tickers2ids(self, tickers):
        """Helper routine that take a sequence of ticker symbols and returns
        their corresponding QT symbol ids.
        """
        data = await self.api.symbols(names=','.join(tickers))
        symbols2ids = {}
        for ticker, symbol in zip(tickers, data['symbols']):
            symbols2ids[symbol['symbol']] = symbol['symbolId']

        return symbols2ids

    async def quote(self, tickers):
        """Return quotes for each ticker in ``tickers``.
        """
        t2ids = await self.tickers2ids(tickers)
        ids = ','.join(map(str, t2ids.values()))
        return (await self.api.quotes(ids=ids))['quotes']

    async def symbols(self, tickers):
        """Return quotes for each ticker in ``tickers``.
        """
        t2ids = await self.tickers2ids(tickers)
        ids = ','.join(map(str, t2ids.values()))
        symbols = {}
        for pkt in (await self.api.symbols(ids=ids))['symbols']:
            symbols[pkt['symbol']] = pkt

        return symbols


class _API:
    """Questrade API endpoints exposed as methods and wrapped with an
    http session.
    """
    def __init__(self, session: asks.Session):
        self._sess = session

    async def _request(self, path: str, params=None) -> dict:
        resp = await self._sess.get(path=f'/{path}', params=params)
        return resproc(resp)

    async def accounts(self) -> dict:
        return await self._request('accounts')

    async def time(self) -> dict:
        return await self._request('time')

    async def markets(self) -> dict:
        return await self._request('markets')

    async def search(self, prefix: str) -> dict:
        return await self._request(
            'symbols/search', params={'prefix': prefix})

    async def symbols(self, ids: str = '', names: str = '') -> dict:
        log.debug(f"Symbol lookup for {ids}")
        return await self._request(
            'symbols', params={'ids': ids, 'names': names})

    async def quotes(self, ids: str) -> dict:
        return await self._request('markets/quotes', params={'ids': ids})

    async def candles(self, id: str, start: str, end, interval) -> dict:
        return await self._request(f'markets/candles/{id}', params={})

    async def balances(self, id: str) -> dict:
        return await self._request(f'accounts/{id}/balances')

    async def postions(self, id: str) -> dict:
        return await self._request(f'accounts/{id}/positions')


async def token_refresher(client):
    """Coninually refresh the ``access_token`` near its expiry time.
    """
    while True:
        await trio.sleep(
            float(client.access_data['expires_at']) - time.time() - .1)
        await client.ensure_access(force_refresh=True)


def _token_from_user(conf: 'configparser.ConfigParser') -> None:
    """Get API token from the user on the console.
    """
    refresh_token = input("Please provide your Questrade access token: ")
    conf['questrade'] = {'refresh_token': refresh_token}


def get_config() -> "configparser.ConfigParser":
    conf, path = config.load()
    if not conf.has_section('questrade') or (
        not conf['questrade'].get('refresh_token')
    ):
        log.warn(
            f"No valid refresh token could be found in {path}")
        _token_from_user(conf)

    return conf


def write_conf(client):
    """Save access creds to config file.
    """
    client._conf['questrade'] = client.access_data
    config.write(client._conf)


@asynccontextmanager
async def get_client() -> Client:
    """Spawn a broker client.
    """
    conf = get_config()
    log.debug(f"Loaded config:\n{colorize_json(dict(conf['questrade']))}")
    client = Client(conf)
    await client.ensure_access()

    try:
        log.debug("Check time to ensure access token is valid")
        try:
            await client.api.time()
        except Exception as err:
            # access token is likely no good
            log.warn(f"Access token {client.access_data['access_token']} seems"
                     f" expired, forcing refresh")
            await client.ensure_access(force_refresh=True)
            await client.api.time()

        accounts = await client.api.accounts()
        log.info(f"Available accounts:\n{colorize_json(accounts)}")
        yield client
    finally:
        write_conf(client)


async def serve_forever(tasks) -> None:
    """Start up a client and serve until terminated.
    """
    async with get_client() as client:
        # pretty sure this doesn't work
        # await client._revoke_auth_token()

        async with trio.open_nursery() as nursery:
            # launch token manager
            nursery.start_soon(token_refresher, client)

            # launch children
            for task in tasks:
                nursery.start_soon(task, client)


async def poll_tickers(
    client: Client, tickers: [str],
    q: trio.Queue,
    rate: int = 5,  # 200ms delay between quotes
    diff_cached: bool = True,  # only deliver "new" quotes to the queue
) -> None:
    """Stream quotes for a sequence of tickers at the given ``rate``
    per second.
    """
    t2ids = await client.tickers2ids(tickers)
    ids = ','.join(map(str, t2ids.values()))
    sleeptime = 1. / rate
    _cache = {}

    while True:  # use an event here to trigger exit?
        quotes_resp = await client.api.quotes(ids=ids)
        start = time.time()
        quotes = quotes_resp['quotes']
        # log.trace(quotes)

        payload = []
        for quote in quotes:

            if quote['delay'] > 0:
                log.warning(f"Delayed quote:\n{quote}")

            if diff_cached:
                # if cache is enabled then only deliver "new" changes
                symbol = quote['symbol']
                last = _cache.setdefault(symbol, {})
                new = set(quote.items()) - set(last.items())
                if new:
                    log.info(
                        f"New quote {quote['symbol']}:\n{new}")
                    _cache[symbol] = quote
                    payload.append(quote)
            else:
                payload.append(quote)

        if payload:
            q.put_nowait(payload)

        proc_time = time.time() - start
        delay = sleeptime - proc_time
        if delay <= 0:
            log.warn(f"Took {proc_time} seconds for processing quotes?")
        else:
            await trio.sleep(delay)


async def api(methname: str, **kwargs) -> dict:
    """Make (proxy through) an api call by name and return its result.
    """
    async with get_client() as client:
        meth = getattr(client.api, methname, None)
        if meth is None:
            log.error(f"No api method `{methname}` could be found?")
            return
        elif not kwargs:
            # verify kwargs requirements are met
            sig = inspect.signature(meth)
            if sig.parameters:
                log.error(
                    f"Argument(s) are required by the `{methname}` method: "
                    f"{tuple(sig.parameters.keys())}")
                return

        return await meth(**kwargs)


async def quote(tickers: [str]) -> dict:
    """Return quotes dict for ``tickers``.
    """
    async with get_client() as client:
        return await client.quote(tickers)
