from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class YatraFlowAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="yatraflow-tests-")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = os.environ.copy()
        env.update({
            "PORT": str(cls.port),
            "YATRAFLOW_DB_ENGINE": "sqlite",
            "YATRAFLOW_SQLITE_PATH": str(Path(cls.temp.name) / "api-tests.sqlite3"),
            # A caller must not be able to relabel SQLite sample rows as configured.
            "YATRAFLOW_DATA_MODE": "configured",
            "YATRAFLOW_MOBILE_API_KEY": "unit-test-integration-key",
            "PYTHONPATH": str(ROOT),
        })
        cls.proc = subprocess.Popen(
            [sys.executable, "-u", "server.py"], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError("API test server stopped during startup")
            try:
                status, _, payload = cls.request("/api/health")
                if status == 200 and payload.get("schema_ready"):
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("API test server failed to become healthy")

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "proc", None) and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
                cls.proc.wait(timeout=5)
        cls.temp.cleanup()

    @classmethod
    def request(cls, path: str, method: str = "GET", body=None, cookie: str | None = None, headers=None):
        request_headers = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            request_headers["Origin"] = cls.base
        if cookie:
            request_headers["Cookie"] = cookie
        req = urllib.request.Request(cls.base + path, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read()
                return response.status, response.headers, json.loads(raw or b"{}")
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                payload = {}
            return error.code, error.headers, payload

    @classmethod
    def static_request(cls, path: str, method: str = "GET"):
        req=urllib.request.Request(cls.base+path,method=method)
        try:
            with urllib.request.urlopen(req,timeout=5) as response:
                return response.status,response.read(),response.headers
        except urllib.error.HTTPError as error:
            return error.code,error.read(),error.headers

    @classmethod
    def login(cls, email="admin@yatraflow.test", password="YatraDemo!2026"):
        status, headers, payload = cls.request("/api/login", "POST", {"email": email, "password": password})
        if status != 200:
            raise AssertionError(f"login failed: HTTP {status}: {payload}")
        return headers.get("Set-Cookie", "").split(";", 1)[0], payload["user"]

    def mobile_post(self, path: str, body: dict):
        return self.request(path, "POST", body, headers={"X-YatraFlow-Integration-Key": "unit-test-integration-key"})

    def test_static_server_exposes_only_dashboard_assets(self):
        status, body, headers=self.static_request("/")
        self.assertEqual(status,200)
        self.assertIn(b"YatraFlow",body)
        self.assertIn("text/html",headers.get("Content-Type",""))
        self.assertEqual(self.static_request("/app.js")[0],200)
        for path in ["/server.py","/backend_core.py","/.env.example","/data/yatraflow.sqlite3","/sqlserver/schema.sql"]:
            self.assertEqual(self.static_request(path)[0],404,path)
        self.assertEqual(self.static_request("/data/yatraflow.sqlite3","HEAD")[0],404)

    def test_authentication_health_and_logout(self):
        status, _, health = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["sample_data"])
        self.assertFalse(health["push_delivery_available"])
        self.assertFalse(health["emergency_dispatch_available"])
        self.assertFalse(health["forecast_available"])
        self.assertEqual(self.request("/api/dashboard?site_id=1")[0], 401)
        self.assertEqual(self.request("/api/sites")[0], 401)
        self.assertEqual(self.request("/api/login", "POST", {"email": "admin@yatraflow.test", "password": "invalid"})[0], 401)
        cookie, user = self.login()
        self.assertEqual(user["role_key"], "super_admin")
        self.assertTrue(self.request("/api/session", cookie=cookie)[2]["authenticated"])
        self.assertEqual(self.request("/api/logout", "POST", {}, cookie)[0], 200)
        self.assertEqual(self.request("/api/dashboard?site_id=1", cookie=cookie)[0], 401)

    def test_development_metrics_and_no_unverified_forecast(self):
        cookie, _ = self.login()
        status, _, dashboard = self.request("/api/dashboard?site_id=1", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(dashboard["data_mode"], "demo")
        self.assertEqual(dashboard["metrics"]["occupancy_source"], "Development sample data")
        self.assertIsNone(dashboard["metrics"]["predicted_tomorrow"])
        self.assertFalse(dashboard["metrics"]["forecast_available"])
        analytics = self.request("/api/analytics?site_id=1&days=14&aggregation=daily", cookie=cookie)[2]
        self.assertEqual(analytics["days"], 14)
        self.assertFalse(analytics["forecast"]["available"])
        self.assertTrue(all(value is None for value in analytics["series"]["forecast_2026"]))
        self.assertEqual(analytics["data_mode"], "demo")

    def test_incident_lifecycle_assignment_notes_and_pending_dispatch(self):
        cookie, user = self.login()
        incident_id = "TEST-SOS-" + uuid.uuid4().hex[:8].upper()
        status, _, received = self.mobile_post("/api/integrations/mobile/sos", {
            "site_id": 1, "external_id": incident_id, "title": "Integration test SOS",
            "description": "Test record only", "zone_name": "Main Gate",
        })
        self.assertEqual(status, 201)
        self.assertEqual(received["status"], "Submitted")
        for next_status in ["Awaiting Acknowledgement", "Acknowledged"]:
            status, _, result = self.request(f"/api/incidents/{incident_id}/transition", "POST", {
                "site_id": 1, "status": next_status, "note": "Test transition",
            }, cookie)
            self.assertEqual(status, 200, result)
        status, _, assigned = self.request(f"/api/incidents/{incident_id}/assign", "POST", {
            "site_id": 1, "responder": "Test responder",
        }, cookie)
        self.assertEqual(status, 200, assigned)
        self.assertFalse(assigned["item"]["external_dispatch_sent"])
        status, _, dispatch = self.request(f"/api/incidents/{incident_id}/dispatch", "POST", {
            "site_id": 1, "responder": "Test responder", "service": "medical",
        }, cookie)
        self.assertEqual(status, 202)
        self.assertFalse(dispatch["external_action_sent"])
        self.assertEqual(dispatch["item"]["request_status"], "pending_external_integration")
        self.assertEqual(self.request(f"/api/incidents/{incident_id}/dispatch", "POST", {
            "site_id": 1, "responder": "Test responder",
        }, cookie)[0], 409)
        status, _, note = self.request(f"/api/incidents/{incident_id}/notes", "POST", {
            "site_id": 1, "note": "Persisted test note",
        }, cookie)
        self.assertEqual(status, 201, note)
        detail = self.request(f"/api/incidents/{incident_id}?site_id=1", cookie=cookie)[2]
        self.assertEqual(detail["status"], "Assigned")
        self.assertEqual(detail["notes"][0]["note"], "Persisted test note")
        self.assertGreaterEqual(len(detail["status_history"]), 4)
        audit = self.request("/api/audit?site_id=1", cookie=cookie)[2]["items"]
        self.assertTrue(any(row["entity_id"] == incident_id and row["action"] == "dispatch_request_recorded" for row in audit))

    def test_broadcast_is_persisted_but_not_claimed_delivered(self):
        cookie, _ = self.login()
        payload = {"site_id": 1, "title": "Test broadcast", "message": "This is a database test.",
                   "category": "Safety", "severity": "urgent", "audience": "zone:Main Gate", "zone_name": "Main Gate"}
        status, _, response = self.request("/api/broadcasts", "POST", payload, cookie)
        self.assertEqual(status, 201, response)
        item = response["item"]
        self.assertFalse(item["push_delivered"])
        self.assertIn(item["delivery_status"], {"queued_for_mobile_polling", "stored_pending_integration"})
        records = self.request("/api/broadcasts?site_id=1", cookie=cookie)[2]["items"]
        self.assertTrue(any(row["id"] == item["id"] and row["reached"] == 0 for row in records))
        self.assertEqual(self.request("/api/broadcasts", "POST", {**payload,"audience":"zone:Not a site zone","zone_name":"Not a site zone"}, cookie)[0], 400)

    def test_rbac_site_scope_and_mobile_ingestion(self):
        super_cookie, _ = self.login()
        email = f"operator-{uuid.uuid4().hex[:8]}@example.test"
        password = "Operator-Test-Password-2026!"
        status, _, result = self.request("/api/admins", "POST", {
            "site_id": 1, "full_name": "Unit Test Operator", "email": email,
            "role_key": "emergency_operator", "password": password,
        }, super_cookie)
        self.assertEqual(status, 201, result)
        operator_cookie, operator = self.login(email, password)
        self.assertEqual(operator["role_key"], "emergency_operator")
        self.assertEqual(self.request("/api/incidents?site_id=1", cookie=operator_cookie)[0], 200)
        self.assertEqual(self.request("/api/admins?site_id=1", cookie=operator_cookie)[0], 403)
        self.assertEqual(self.request("/api/dashboard?site_id=2", cookie=operator_cookie)[0], 403)
        self.assertEqual(self.request("/api/incidents", "POST", {}, operator_cookie)[0], 404)

        visitor_id = "TEST-VISITOR-" + uuid.uuid4().hex[:8].upper()
        session_id = "TEST-SESSION-" + uuid.uuid4().hex[:8].upper()
        status, _, created = self.mobile_post("/api/integrations/mobile/visitors", {
            "site_id": 1, "external_id": visitor_id, "full_name": "Test Visitor",
            "pass_id": visitor_id, "zone_name": "Main Gate", "checked_in_at": "2026-11-25T10:30:00+05:30",
            "session_id": session_id,
        })
        self.assertEqual(status, 201, created)
        visitors = self.request(f"/api/visitors?site_id=1&q={visitor_id}", cookie=super_cookie)[2]["items"]
        self.assertEqual(visitors[0]["status"], "Checked in")
        self.assertFalse(visitors[0]["needs_assistance"])
        sos_id="VISITOR-LINKED-SOS-"+uuid.uuid4().hex[:8].upper()
        sos_status,_,_=self.mobile_post("/api/integrations/mobile/sos",{
            "site_id":1,"external_id":sos_id,"visitor_id":visitor_id,"zone_name":"Main Gate",
            "title":"Visitor requested assistance","description":"Test-only linked assistance record",
        })
        self.assertEqual(sos_status,201)
        assistance=self.request(f"/api/visitors?site_id=1&q={visitor_id}&filter=assistance",cookie=super_cookie)[2]
        self.assertEqual(len(assistance["items"]),1)
        self.assertTrue(assistance["items"][0]["needs_assistance"])
        self.assertGreaterEqual(assistance["summary"]["needs_assistance"],1)
        status_filter=self.request(f"/api/visitors?site_id=1&q={visitor_id}&status=Assistance",cookie=super_cookie)[2]
        self.assertEqual([item["id"] for item in status_filter["items"]],[visitor_id])
        vip_id="VIP-VISITOR-"+uuid.uuid4().hex[:8].upper()
        vip_status,_,vip_created=self.mobile_post("/api/integrations/mobile/visitors",{
            "site_id":1,"external_id":vip_id,"full_name":"Test VIP","pass_type":"VIP",
        })
        self.assertEqual(vip_status,201,vip_created)
        vip_result=self.request("/api/visitors?site_id=1&filter=vip",cookie=super_cookie)[2]
        self.assertTrue(any(item["id"]==vip_id for item in vip_result["items"]))
        self.assertGreaterEqual(vip_result["summary"]["vip_passes"],1)
        self.assertEqual(self.mobile_post("/api/integrations/mobile/visitors", {
            "site_id": 1, "external_id": visitor_id, "full_name": "Test Visitor",
            "pass_id": visitor_id, "zone_name": "Main Gate", "checked_in_at": "2026-11-25T10:30:00+05:30",
            "session_id": session_id,
        })[2]["updated"], True)
        self.assertEqual(self.request("/api/integrations/mobile/notifications?site_id=1&visitor_id="+visitor_id+"&zone_name=Main%20Gate", headers={"X-YatraFlow-Integration-Key":"unit-test-integration-key"})[0], 200)
        self.assertEqual(self.request("/api/integrations/mobile/sos", "POST", {"site_id":1,"external_id":"blocked"})[0], 401)
        crowd_status, _, crowd = self.mobile_post("/api/integrations/mobile/crowd", {
            "site_id": 1, "zone_name": "Main Gate", "visitor_count": 2700, "capacity": 1,
            "observed_at": "2026-11-25T11:00:00+05:30", "source": "unit test",
        })
        self.assertEqual(crowd_status, 201, crowd)
        self.assertEqual(crowd["density_percent"], 90.0)
        self.assertEqual(self.request("/api/zones/101?site_id=1",cookie=super_cookie)[2]["capacity"],3000)
        self.assertEqual(self.mobile_post("/api/integrations/mobile/crowd", {
            "site_id": 1, "zone_name": "Missing zone", "visitor_count": 1,
        })[0], 404)

    def test_mobile_notification_polling_requires_matching_site_visitor_and_ack(self):
        cookie,_=self.login()
        visitor_id="POLL-VISITOR-"+uuid.uuid4().hex[:8].upper()
        status,_,created=self.mobile_post("/api/integrations/mobile/visitors",{
            "site_id":1,"external_id":visitor_id,"full_name":"Polling Test Visitor","zone_name":"Main Gate",
        })
        self.assertEqual(status,201,created)
        headers={"X-YatraFlow-Integration-Key":"unit-test-integration-key"}
        self.assertEqual(self.request("/api/integrations/mobile/notifications",headers=headers)[0],400)
        self.assertEqual(self.request("/api/integrations/mobile/notifications?site_id=1",headers=headers)[0],400)
        self.assertEqual(self.request("/api/integrations/mobile/notifications?site_id=1&visitor_id=missing",headers=headers)[0],404)
        self.assertEqual(self.request("/api/visitors?site_id=1&filter=not-a-filter",cookie=cookie)[0],400)
        self.assertEqual(self.request(f"/api/integrations/mobile/notifications?site_id=2&visitor_id={visitor_id}",headers=headers)[0],404)
        payload={"site_id":1,"title":"Polling test alert","message":"Stored for polling only.","audience":"all_visitors","category":"Information","severity":"info"}
        status,_,response=self.request("/api/broadcasts","POST",payload,cookie)
        self.assertEqual(status,201,response)
        notification_id=response["item"]["notification_id"]
        query=urllib.parse.urlencode({"site_id":1,"visitor_id":visitor_id,"zone_name":"Main Gate"})
        status,_,feed=self.request("/api/integrations/mobile/notifications?"+query,headers=headers)
        self.assertEqual(status,200,feed)
        self.assertTrue(any(item["id"]==notification_id for item in feed["items"]))
        main_zone_payload={"site_id":1,"title":"Main Gate zone alert","message":"Zone-scoped test only.","audience":"zone:Main Gate","zone_name":"Main Gate","category":"Crowd","severity":"warning"}
        main_zone_status,_,main_zone_response=self.request("/api/broadcasts","POST",main_zone_payload,cookie)
        self.assertEqual(main_zone_status,201,main_zone_response)
        _,_,zone_feed=self.request("/api/integrations/mobile/notifications?"+query,headers=headers)
        self.assertTrue(any(item["id"]==main_zone_response["item"]["notification_id"] for item in zone_feed["items"]))
        zone_payload={"site_id":1,"title":"Courtyard zone alert","message":"Zone-scoped test only.","audience":"zone:Courtyard","zone_name":"Courtyard","category":"Crowd","severity":"warning"}
        zone_status,_,zone_response=self.request("/api/broadcasts","POST",zone_payload,cookie)
        self.assertEqual(zone_status,201,zone_response)
        # A client-supplied zone must not let a visitor in Main Gate poll or acknowledge Courtyard alerts.
        forged_query=urllib.parse.urlencode({"site_id":1,"visitor_id":visitor_id,"zone_name":"Courtyard"})
        _,_,forged_feed=self.request("/api/integrations/mobile/notifications?"+forged_query,headers=headers)
        self.assertFalse(any(item["id"]==zone_response["item"]["notification_id"] for item in forged_feed["items"]))
        self.assertEqual(self.request(f"/api/integrations/mobile/notifications/{zone_response['item']['notification_id']}/ack","POST",{"visitor_id":visitor_id},headers=headers)[0],404)
        staff_status,_,staff_notice=self.request("/api/alerts/notify","POST",{
            "site_id":1,"category":"Safety","title":"Staff-only test","message":"Not for visitor polling.",
            "severity":"urgent","zone_name":"Main Gate",
        },cookie)
        self.assertEqual(staff_status,201,staff_notice)
        _,_,visitor_feed=self.request("/api/integrations/mobile/notifications?"+query,headers=headers)
        self.assertFalse(any(item["id"]==staff_notice["item"]["id"] for item in visitor_feed["items"]))
        self.assertEqual(self.request(f"/api/integrations/mobile/notifications/{staff_notice['item']['id']}/ack","POST",{"visitor_id":visitor_id},headers=headers)[0],404)
        status,_,ack=self.request(f"/api/integrations/mobile/notifications/{notification_id}/ack","POST",{"visitor_id":visitor_id},headers=headers)
        self.assertEqual(status,200,ack)
        self.assertEqual(ack["delivery_status"],"acknowledged")
        repeated_status,_,repeated=self.request(f"/api/integrations/mobile/notifications/{notification_id}/ack","POST",{"visitor_id":visitor_id},headers=headers)
        self.assertEqual(repeated_status,200,repeated)
        self.assertTrue(repeated["already_acknowledged"])
        self.assertEqual(repeated["delivery_id"],ack["delivery_id"])
        audit=self.request("/api/audit?site_id=1",cookie=cookie)[2]["items"]
        self.assertTrue(any(row["entity_id"]==notification_id and row["action"]=="acknowledged" for row in audit))

    def test_visitor_checkout_is_atomic_and_zone_detail_is_scoped(self):
        cookie,_=self.login()
        visitor_id="CHECKOUT-"+uuid.uuid4().hex[:8].upper()
        session_id="CHECKIN-"+uuid.uuid4().hex[:8].upper()
        status,_,created=self.mobile_post("/api/integrations/mobile/visitors",{
            "site_id":1,"external_id":visitor_id,"full_name":"Checkout Test","zone_name":"Main Gate",
            "checked_in_at":"2026-11-25T09:00:00+05:30","session_id":session_id,
        })
        self.assertEqual(status,201,created)
        status,_,zone=self.request("/api/zones/101?site_id=1",cookie=cookie)
        self.assertEqual(status,200,zone)
        self.assertEqual(zone["name"],"Main Gate")
        self.assertIn("recent_trend",zone)
        self.assertEqual(self.request("/api/zones/101?site_id=2",cookie=cookie)[0],404)
        status,_,checked=self.request(f"/api/visitors/{visitor_id}/checkout","POST",{"site_id":1},cookie)
        self.assertEqual(status,200,checked)
        self.assertEqual(self.request(f"/api/visitors/{visitor_id}/checkout","POST",{"site_id":1},cookie)[0],409)
        detail=self.request(f"/api/visitors/{visitor_id}?site_id=1",cookie=cookie)[2]
        self.assertEqual(detail["sessions"][0]["checked_out_at"],checked["checked_out_at"])
        records=self.request(f"/api/visitors?site_id=1&q={visitor_id}",cookie=cookie)[2]
        self.assertEqual(records["items"][0]["status"],"Checked out")
        audit=self.request("/api/audit?site_id=1",cookie=cookie)[2]["items"]
        self.assertTrue(any(row["entity_id"]==visitor_id and row["action"]=="checked_out" for row in audit))

    def test_sql_injection_and_invalid_transition_are_rejected(self):
        cookie, _ = self.login()
        self.assertEqual(self.request("/api/dashboard?site_id=1%20OR%201%3D1", cookie=cookie)[0], 400)
        incident_id = "TEST-SOS-" + uuid.uuid4().hex[:8].upper()
        self.assertEqual(self.mobile_post("/api/integrations/mobile/sos", {
            "site_id": 1, "external_id": incident_id, "title": "Test", "description": "Test",
        })[0], 201)
        self.assertEqual(self.request(f"/api/incidents/{incident_id}/transition", "POST", {
            "site_id": 1, "status": "Resolved", "note": "Premature resolve",
        }, cookie)[0], 409)
        self.assertEqual(self.request(f"/api/incidents/{incident_id}/transition", "POST", {
            "site_id": 1, "status": "Awaiting Acknowledgement", "note": "",
        }, cookie)[0], 200)
        self.assertEqual(self.request(f"/api/incidents/{incident_id}/transition", "POST", {
            "site_id": 1, "status": "Acknowledged", "note": "Confirmed",
        }, cookie)[0], 200)
        self.assertEqual(self.request(f"/api/incidents/{incident_id}/assign", "POST", {
            "site_id": 1, "responder": "Operator",
        }, cookie)[0], 200)
        self.assertEqual(self.request(f"/api/incidents/{incident_id}/transition", "POST", {
            "site_id": 1, "status": "Resolved", "note": "",
        }, cookie)[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
