import yfinance as yf

tickers = ["7203.T", "6758.T", "8035.T"]
data = yf.download(tickers, period="1mo")
print(data["Close"].tail())
