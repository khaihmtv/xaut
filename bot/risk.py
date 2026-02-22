from config import *

class RiskManager:

    def __init__(self, client):
        self.client = client

    def check_equity(self):
        equity = self.client.get_equity()
        if equity <= TOTAL_CAPITAL * (1 - KILL_SWITCH_DRAWDOWN):
            return False
        return True

    def check_funding(self):
        funding = self.client.get_funding_rate()
        if abs(funding) > MAX_FUNDING:
            return False
        return True