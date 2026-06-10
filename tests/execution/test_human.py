"""真人节奏测试:注入种子 + 假 sleep,验证可复现、有界、零真实等待。"""

from src.execution.human import HumanPacer, PacingProfile


def make_pacer(seed: int = 42):
    slept: list[float] = []
    pacer = HumanPacer(seed=seed, sleep_fn=slept.append)
    return pacer, slept


class TestHumanPacer:
    def test_same_seed_reproducible(self):
        p1, s1 = make_pacer(7)
        p2, s2 = make_pacer(7)
        for p in (p1, p2):
            p.between_actions()
            p.before_commit()
            p.after_navigation()
        assert s1 == s2

    def test_delays_within_profile_bounds(self):
        profile = PacingProfile()
        pacer, slept = make_pacer()
        for _ in range(200):
            pacer.between_actions()
        assert all(profile.action_min <= d <= profile.action_max for d in slept)

    def test_before_commit_is_slower_thinking_pause(self):
        profile = PacingProfile()
        pacer, slept = make_pacer()
        for _ in range(50):
            pacer.before_commit()
        assert all(profile.think_min <= d <= profile.think_max for d in slept)

    def test_keystroke_delays_one_per_char(self):
        pacer, _ = make_pacer()
        text = "NVDA 100"
        delays = pacer.keystroke_delays(text)
        assert len(delays) == len(text)
        p = pacer.profile
        assert all(p.type_char_min <= d <= p.type_char_max for d in delays)

    def test_pause_keystroke_sleeps_exact_value(self):
        pacer, slept = make_pacer()
        pacer.pause_keystroke(0.123)
        assert slept == [0.123]

    def test_delays_vary_not_constant(self):
        pacer, slept = make_pacer()
        for _ in range(20):
            pacer.between_actions()
        assert len(set(slept)) > 1  # 真人不会每次间隔完全一样
