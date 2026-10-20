import pytest

from app.services.player import PlayerController, PlayerError
from tests.fake_mpd import FakeMPD


@pytest.fixture
def fake():
    return FakeMPD()


@pytest.fixture
def player(fake):
    return PlayerController("fake", 0, client_factory=fake.factory)


def test_single_dropped_connection_is_retried_transparently(player, fake):
    player.status()
    fake.drop_next_call = True
    player.play(40)
    assert fake.state == "play"
    assert fake.connects == 2


def test_connection_reused_between_calls(player, fake):
    player.status()
    player.play(40)
    player.pause()
    assert fake.connects == 1


def test_mute_twice_keeps_original_volume(player, fake):
    fake.volume = 33
    player.mute()
    player.mute()
    assert player.unmute(max_volume=100).applied == 33


def test_unmute_when_not_muted_is_noop(player, fake):
    assert player.unmute(max_volume=40) is None
    assert fake.volume == 20


def test_set_volume_clears_mute(player, fake):
    player.mute()
    player.set_volume(10, max_volume=40)
    assert player.status()["muted"] is False and fake.volume == 10


def test_no_mixer_reports_none_volume_and_mute_fails(player, fake):
    fake.volume = -1
    assert player.status()["volume"] is None
    with pytest.raises(PlayerError) as e:
        player.mute()
    assert e.value.code == "NO_MIXER"


def test_status_when_down(player, fake):
    fake.down = True
    st = player.status()
    assert st["connected"] is False and st["outputs"] == []
