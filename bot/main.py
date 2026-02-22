import time
from okx_client import OKXClient
from engine import GridEngine
from risk import RiskManager

client = OKXClient()
engine = GridEngine(client)
risk = RiskManager(client)

def run():
    while True:

        if not risk.check_equity():
            print("KILL SWITCH ACTIVATED")
            client.close_all_positions()
            break

        if not risk.check_funding():
            print("Funding too high, skip cycle")
            time.sleep(300)
            continue

        candles = client.get_candles()
        trend, price, spacing, size = engine.analyze(candles)

        orders = engine.build_grid(trend, price, spacing, size)

        client.cancel_all_orders()

        for order in orders:
            client.place_limit_order(
                order["side"],
                order["price"],
                order["size"]
            )

        print("Grid updated")
        time.sleep(300)

if __name__ == "__main__":
    run()