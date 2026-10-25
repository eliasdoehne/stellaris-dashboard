from stellarisdashboard import dashboard_app


def test_dash_app_serves_first_request_without_start_dash_app():
    client = dashboard_app.flask_app.test_client()

    assert client.get("/timeline/").status_code == 200
    assert client.get("/timeline/_dash-layout").status_code == 200
