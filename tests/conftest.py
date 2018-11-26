import pytest


@pytest.fixture
def brokerconf():
    from piker.brokers import config
    return config.load()[0]


@pytest.fixture
def us_symbols():
    return ['TSLA', 'AAPL', 'CGC', 'CRON']


@pytest.fixture
def tmx_symbols():
    return ['APHA.TO', 'WEED.TO', 'ACB.TO']


@pytest.fixture
def cse_symbols():
    return ['TRUL.CN', 'CWEB.CN', 'SNN.CN']