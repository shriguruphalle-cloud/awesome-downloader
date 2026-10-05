"""Magnet links default to this app, and Windows can list it in Default apps.

register() writes the handler AND the Capabilities / RegisteredApplications
declaration that puts Awesome Downloader in Settings > Default apps (without
it, the app couldn't be picked there when Windows had magnet: locked to
another client); unregister() takes all of it back. Run against an in-memory
stand-in for winreg -- no test touches the real registry."""
import sys
import types

import _support
from _support import check

from app.utils import protocol_handler as ph

store = {}   # path -> {value name: data}


class Key:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def CreateKey(root, path):
    store.setdefault(path.lower(), {})
    return Key(path.lower())


def OpenKey(root, path, *a):
    if path.lower() not in store:
        raise FileNotFoundError(path)
    return Key(path.lower())


def SetValueEx(key, name, _r, _t, data):
    store[key.path][name] = data


def QueryValueEx(key, name):
    if name not in store[key.path]:
        raise FileNotFoundError(name)
    return store[key.path][name], 1


def DeleteKey(root, path):
    if path.lower() not in store:
        raise FileNotFoundError(path)
    del store[path.lower()]


def DeleteValue(key, name):
    del store[key.path][name]


fake = types.SimpleNamespace(HKEY_CURRENT_USER=1, REG_SZ=1, KEY_SET_VALUE=2, CreateKey=CreateKey, OpenKey=OpenKey,
                             SetValueEx=SetValueEx, QueryValueEx=QueryValueEx, DeleteKey=DeleteKey, DeleteValue=DeleteValue)
sys.modules["winreg"] = fake

check(not ph.is_default(), "reported default before registering")
check(ph.register(), "register failed")
check(ph.is_default(), "not the default right after registering")
caps = store.get(r"software\awesomedownloader\capabilities", {})
check(caps.get("ApplicationName") == ph.REGISTERED_NAME, "no Capabilities: the app can't be picked in Default apps")
check(store.get(r"software\awesomedownloader\capabilities\urlassociations", {}).get("magnet") == "AwesomeDownloader.Magnet",
      "Capabilities don't claim magnet:")
check(store.get(r"software\registeredapplications", {}).get(ph.REGISTERED_NAME) == r"Software\AwesomeDownloader\Capabilities",
      "not listed in RegisteredApplications")
print("register: handler + Default apps declaration")

check(ph.unregister(), "unregister failed")
check(not ph.is_default(), "still the default after unregistering")
left = [k for k in store if "awesomedownloader" in k] + \
       [n for n in store.get(r"software\registeredapplications", {}) if n == ph.REGISTERED_NAME]
check(not left, "unregister left keys behind: %s" % left)
print("unregister: all of it gone")

# A locked choice: the button opens Default apps on this app's page.
opened = []
ph.os = types.SimpleNamespace(startfile=opened.append, path=ph.os.path, environ=ph.os.environ)
check(ph.open_default_apps() and opened and opened[0].startswith("ms-settings:defaultapps?registeredAppUser=Awesome%20Downloader"),
      "Default apps link wrong: %s" % opened)
import os  # noqa: E402
iss = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "installer.iss"),
           encoding="utf-8-sig").read()
check("RegisteredApplications" in iss and "Capabilities\\URLAssociations" in iss, "the installer doesn't declare the app to Default apps")
print("Default apps: opened on this app's page; installer declares it too")
print("\nMAGNET DEFAULT OK")
