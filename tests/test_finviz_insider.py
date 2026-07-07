import pytest
import src.sources.finviz_insider as insider_mod
from src.sources.finviz_insider import FinvizInsiderSource
from src.models import NewsItem

PORTFOLIO_CSV_FIXTURE = (
    "Ticker,Company,Price,Change%\n"
    "NVDA,NVIDIA Corp,195.55,+0.37%\n"
    "TSLA,Tesla Inc,419.77,+6.69%\n"
)

INSIDER_CSV_FIXTURE = (
    "Ticker,Owner,Owner CIK,Relationship,Date,Transaction,Cost,#Shares,Value ($),#Shares Total,SEC Form 4,SEC Form 4 Link\n"
    '"NVDA","HUANG JEN HSUN",123456,"CEO",07/02/2026,Sale,195.00,10000,1950000,1000000,07/06/2026 21:40,http://sec.gov/form4/nvda1\n'
    '"NVDA","SOME DIRECTOR",123457,"Director",07/02/2026,Sale,195.00,1000,195000,10000,07/06/2026 21:40,http://sec.gov/form4/nvda2\n'
    '"TSLA","MUSK ELON",654321,"10% Owner",07/02/2026,Buy,400.00,5000,2000000,50000000,07/06/2026 21:30,http://sec.gov/form4/tsla1\n'
    '"AAPL","COOK TIMOTHY D",111111,"CEO",07/02/2026,Sale,200.00,15000,3000000,2000000,07/06/2026 21:20,http://sec.gov/form4/aapl1\n'
)

class _FakeResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.encoding = "utf-8"

def test_finviz_insider_source_fetches_and_filters(monkeypatch):
    monkeypatch.setenv("FINVIZ_AUTH_TOKEN", "tok")
    
    def mock_get(url, params=None, timeout=None):
        if "portfolio" in url:
            assert params["pid"] == "1001063054"
            return _FakeResp(PORTFOLIO_CSV_FIXTURE)
        elif "insiders" in url:
            assert params["tc"] == "7"
            return _FakeResp(INSIDER_CSV_FIXTURE)
        return _FakeResp("", 404)
        
    monkeypatch.setattr(insider_mod.requests, "get", mock_get)

    src = FinvizInsiderSource(pid="1001063054", min_value=1000000.0)
    items = src.fetch()

    # NVDA ($1.95M Sale) and TSLA ($2.0M Buy) should be parsed and matched.
    # NVDA ($195K Sale) is under $1M -> filtered out.
    # AAPL ($3.0M Sale) is not in portfolio -> filtered out.
    assert len(items) == 2

    nvda = next(i for i in items if i.ticker == "NVDA")
    assert nvda.keep is True
    assert nvda.urgency == "high"
    assert nvda.publisher == "HUANG JEN HSUN"
    assert nvda.headline == "NVDA | Insider Sale of $1,950,000 (10,000 shares) by HUANG JEN HSUN (CEO)"
    assert nvda.url == "http://sec.gov/form4/nvda1"
    assert nvda.id == "http://sec.gov/form4/nvda1"

    tsla = next(i for i in items if i.ticker == "TSLA")
    assert tsla.keep is True
    assert tsla.urgency == "high"
    assert tsla.publisher == "MUSK ELON"
    assert tsla.headline == "TSLA | Insider Buy of $2,000,000 (5,000 shares) by MUSK ELON (10% Owner)"
    assert tsla.url == "http://sec.gov/form4/tsla1"
    assert tsla.id == "http://sec.gov/form4/tsla1"
