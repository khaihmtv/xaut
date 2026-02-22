from config import *

class RiskManager:

    def __init__(self, client, max_drawdown=0.3):
        self.client = client
        self.max_drawdown = max_drawdown
        self.start_equity = self.client.get_equity()

    def check_equity(self):
        equity = self.client.get_equity()

        if equity <= 0:
            print("Equity error")
            return False

        drawdown = (self.start_equity - equity) / self.start_equity

        if drawdown >= self.max_drawdown:
            print("Max drawdown hit. Stop trading.")
            return False

        return True

    def check_funding(self):
        """
        Chặn trade nếu funding quá cao
        """
        try:
            funding = self.client.get_funding_rate()
            print("Current funding:", funding)

            if abs(funding) > 0.0015:  # 0.15%
                print("Funding too high, skip trade.")
                return False

            return True

        except Exception as e:
            print("Funding check error:", e)
            return True