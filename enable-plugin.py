"""Enable the declarative Dank Todo plugin without replacing DMS preferences."""

import json
import os
import shutil
import tempfile
from pathlib import Path


def update_json(path, transform):
    original = path.read_text() if path.exists() else "{}"
    data = json.loads(original)
    before = json.dumps(data, sort_keys=True)
    transform(data)
    if json.dumps(data, sort_keys=True) == before:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
        current = path.read_text() if path.exists() else "{}"
        if current != original:
            raise RuntimeError(f"{path} changed during activation; retry")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def add_widget(settings):
    # Empty/missing barConfigs uses DMS's default bar. Seed only its right
    # section and let DMS supply defaults for the other bar properties.
    bars = settings.get("barConfigs") or [{"id": "default", "name": "Main Bar"}]
    for bar in bars:
        if not bar.get("enabled", True):
            continue
        for section in ("leftWidgets", "centerWidgets", "rightWidgets"):
            for widget in bar.get(section, []):
                widget_id = widget if isinstance(widget, str) else widget.get("id")
                if widget_id == "dankTodo":
                    if isinstance(widget, dict):
                        widget["enabled"] = True
                    break
            else:
                continue
            break
        else:
            bar.setdefault("rightWidgets", [
                "systemTray", "clipboard", "cpuUsage", "memUsage",
                "notificationButton", "battery", "controlCenterButton",
            ]).insert(0, {"id": "dankTodo", "enabled": True})
    settings["barConfigs"] = bars


def link_system_plugin(config, state, system=Path("/etc/xdg/quickshell/dms-plugins/dankTodo")):
    # DMS 1.6.2 forgets the plugin when an overriding user manifest disappears,
    # even if its system manifest remains installed. Keep a stable discovery
    # link to NixOS's plugin instead of removing the user manifest on rebuild.
    local = config / "plugins/dankTodo"
    if not (system / "plugin.json").is_file():
        return
    if local.is_symlink() and os.readlink(local) == str(system):
        return
    if local.exists() or local.is_symlink():
        backup = state / "dms-plugin-backups"
        backup.mkdir(parents=True, exist_ok=True)
        destination = Path(tempfile.mkdtemp(prefix="dankTodo-", dir=backup)) / "plugin"
        shutil.move(str(local), destination)
        print(f"Archived CLI-installed Dank Todo to {destination}")
    local.parent.mkdir(parents=True, exist_ok=True)
    local.symlink_to(system)


def main():
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "DankMaterialShell"
    state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    link_system_plugin(config, state)

    update_json(config / "settings.json", add_widget)
    update_json(config / "plugin_settings.json", lambda settings: settings.setdefault("dankTodo", {}).update(enabled=True))


if __name__ == "__main__":
    main()
