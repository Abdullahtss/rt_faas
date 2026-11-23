import os

os.environ.setdefault("RTFAAS_TOKEN", "rtfaas-dev-token")

from locust import HttpUser, between, task


class IngestUser(HttpUser):
    wait_time = between(0.01, 0.05)

    def on_start(self):
        self.client.headers["Authorization"] = "Bearer rtfaas-dev-token"

    @task
    def ingest(self):
        self.client.post(
            "/api/v1/transactions",
            json={
                "account_id": "ACC-1001",
                "amount": 3500,
                "lat": 24.86,
                "lon": 67.00,
                "city": "Karachi",
                "merchant": "Locust",
            },
        )
