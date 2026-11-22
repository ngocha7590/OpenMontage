"""``backlot open`` under a host app (OpenMontage Studio): print the host's
board URL, never spawn the Backlot server or open a browser tab."""

from backlot import __main__ as cli


def test_hosted_board_url_short_circuits(monkeypatch, capsys):
    monkeypatch.setenv("OPENMONTAGE_BOARD_URL", "http://127.0.0.1:8790/projects/ca-1")
    spawned, opened = [], []
    monkeypatch.setattr(cli, "_spawn_server", lambda port: spawned.append(port))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(cli, "_server_alive", lambda port: False)
    assert cli.main(["open", "ca-1"]) == 0
    assert capsys.readouterr().out.strip() == "backlot: http://127.0.0.1:8790/projects/ca-1"
    assert spawned == [] and opened == []


def test_non_http_board_url_is_ignored(monkeypatch, capsys):
    monkeypatch.setenv("OPENMONTAGE_BOARD_URL", "javascript:alert(1)")
    monkeypatch.setattr(cli, "_server_alive", lambda port: True)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: None)
    assert cli.main(["open", "ca-1"]) == 0
    assert "/p/ca-1" in capsys.readouterr().out  # normal standalone behaviour
