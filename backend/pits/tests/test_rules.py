from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from pits.models import LiquorSample, Pit, Yard
from pits.rules import (
    MAX_PH,
    MIN_PH,
    RuleError,
    assert_can_set_status,
    latest_ph,
)


def make_pit(code: str = "测-1") -> Pit:
    yard = Yard.objects.create(name="测试场")
    return Pit.objects.create(yard=yard, code=code)


def add(pit: Pit, ph: float, ago_minutes: int) -> None:
    """插一条 ago_minutes 分钟前的读数（taken_at 为 auto_now_add，需显式改写）。"""
    sample = LiquorSample.objects.create(pit=pit, ph=ph, operator="t")
    LiquorSample.objects.filter(id=sample.id).update(
        taken_at=timezone.now() - timedelta(minutes=ago_minutes)
    )
    pit.refresh_from_db()


class LatestPhTests(TestCase):
    def test_排头就是时刻最晚那条(self):
        pit = make_pit()
        add(pit, 6.1, 30)
        add(pit, 4.0, 10)  # 最晚一条，在带内
        self.assertEqual(latest_ph(pit), 4.0)


class DrainGateTests(TestCase):
    def test_最晚一条出带_即使中位在带内_也拒(self):
        # 两名值班抢按场景中「只靠中位混过去」那口：中位 4.1 在带内，最晚 6.1 出带
        pit = make_pit()
        add(pit, 4.0, 30)
        add(pit, 4.1, 20)
        add(pit, 6.1, 5)
        with self.assertRaises(RuleError):
            assert_can_set_status(pit, Pit.STATUS_DRAINED)

    def test_最晚一条在带_即使中位出带_也放行(self):
        pit = make_pit()
        add(pit, 2.0, 30)
        add(pit, 2.1, 20)
        add(pit, 4.2, 5)  # 中位 2.1 出带，最晚一条 4.2 在带内
        assert_can_set_status(pit, Pit.STATUS_DRAINED)  # 不抛异常即通过

    def test_边界值放行(self):
        pit = make_pit()
        add(pit, MIN_PH, 5)
        assert_can_set_status(pit, Pit.STATUS_DRAINED)
        add(pit, MAX_PH, 1)
        assert_can_set_status(pit, Pit.STATUS_DRAINED)

    def test_无记录拒放液(self):
        pit = make_pit()
        with self.assertRaises(RuleError):
            assert_can_set_status(pit, Pit.STATUS_DRAINED)

    def test_拨回注液不读放行门槛(self):
        # 拨回 fill / tanning，不得再拿任何读数（含放液那条）做门槛
        pit = make_pit()
        add(pit, 8.0, 5)
        assert_can_set_status(pit, Pit.STATUS_FILL)
        assert_can_set_status(pit, Pit.STATUS_TANNING)

    def test_无效状态(self):
        pit = make_pit()
        with self.assertRaises(RuleError):
            assert_can_set_status(pit, "unknown")
