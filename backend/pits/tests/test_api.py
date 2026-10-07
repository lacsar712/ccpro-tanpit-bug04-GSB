from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from pits.models import LiquorSample, Pit, User, Yard


class ApiTests(TestCase):
    def setUp(self):
        self.user = User(username="admin", role="admin")
        self.user.set_password("123456")
        self.user.save()
        resp = self.client.post(
            "/api/auth/login",
            {"username": "admin", "password": "123456"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.token = resp.json()["access_token"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        yard = Yard.objects.create(name="测试场")
        # 甲坑：最晚读数 4.2 在带内（中位 2.1 出带）→ 应放行
        self.good = Pit.objects.create(yard=yard, code="甲", status=Pit.STATUS_TANNING)
        # 乙坑：最晚读数 6.1 出带，只有中位 4.0 在带内 → 必须拦下
        self.bad = Pit.objects.create(yard=yard, code="乙", status=Pit.STATUS_TANNING)
        for ph, ago in ((2.0, 30), (2.1, 20), (4.2, 5)):
            self._old_sample(self.good, ph, ago)
        for ph, ago in ((4.0, 30), (4.0, 20), (6.1, 5)):
            self._old_sample(self.bad, ph, ago)

    def _old_sample(self, pit, ph, ago_minutes):
        s = LiquorSample.objects.create(pit=pit, ph=ph, operator="t")
        LiquorSample.objects.filter(id=s.id).update(
            taken_at=timezone.now() - timedelta(minutes=ago_minutes)
        )

    def test_两坑抢按_只认最新读数在带的那口(self):
        ok = self.client.post(
            f"/api/pits/{self.good.id}/status",
            {"status": "drained"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        self.good.refresh_from_db()
        self.assertEqual(self.good.status, Pit.STATUS_DRAINED)

        blocked = self.client.post(
            f"/api/pits/{self.bad.id}/status",
            {"status": "drained"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(blocked.status_code, 400)
        self.bad.refresh_from_db()
        self.assertEqual(self.bad.status, Pit.STATUS_TANNING)

    def test_放液后登录态仍在(self):
        r = self.client.post(
            f"/api/pits/{self.good.id}/status",
            {"status": "drained"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(r.status_code, 200)
        me = self.client.get("/api/auth/me", **self.auth)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], "admin")

    def test_贴片与近次排头同为最新一条(self):
        r = self.client.get("/api/board", **self.auth)
        self.assertEqual(r.status_code, 200)
        pits = {p["code"]: p for p in r.json()["pits"]}
        self.assertEqual(pits["甲"]["latestPh"], 4.2)
        self.assertEqual(pits["甲"]["recentSamples"][0]["ph"], 4.2)
        self.assertEqual(pits["乙"]["latestPh"], 6.1)
        self.assertEqual(pits["乙"]["recentSamples"][0]["ph"], 6.1)

    def test_新登记读数立刻成为排头并据此放行(self):
        # 乙坑原本被拦；登记一条带内新读数后，排头变成它，放液放行
        r = self.client.post(
            f"/api/pits/{self.bad.id}/samples",
            {"ph": 4.7},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["latestPh"], 4.7)
        self.assertEqual(r.json()["recentSamples"][0]["ph"], 4.7)
        r2 = self.client.post(
            f"/api/pits/{self.bad.id}/status",
            {"status": "drained"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(r2.status_code, 200, r2.content)

    def test_拨回注液不校验门槛(self):
        self.good.status = Pit.STATUS_DRAINED
        self.good.save(update_fields=["status"])
        LiquorSample.objects.create(pit=self.good, ph=9.9, operator="t")
        r = self.client.post(
            f"/api/pits/{self.good.id}/status",
            {"status": "fill"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(r.status_code, 200, r.content)
