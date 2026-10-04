"""Takes out of the built app the parts of Qt it never uses (~46 MB).

PyInstaller's PySide6 hooks bring along a few things beside the modules the
app imports: a software OpenGL renderer, Qt's own translations, the PDF
image-format plugin and the PDF module behind it, and the on-screen keyboard
input plugin with the Qt Quick/QML it needs. The app paints with QPainter
(no OpenGL), is in English (no QTranslator), reads no PDFs as images, and
has no QML -- so none of it is ever loaded.

Safety first: before anything is deleted, every binary that stays in the
bundle is checked for imports of a DLL on the list. If any still links to
one, nothing is removed and the build fails, naming it -- a removal that
breaks the app is worse than a bigger download.

Run after PyInstaller, from build_exe.bat:  python build_trim.py [app folder]
"""
import glob
import os
import shutil
import sys

from PyInstaller.depend import bindepend

APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join("dist", "Awesome Downloader")
QT = os.path.join(APP, "_internal", "PySide6")

# (path under PySide6\, why it isn't needed)
UNUSED = [
    ("opengl32sw.dll", "software OpenGL for machines without a GPU driver; the app draws with QPainter"),
    ("translations", "Qt's own translations; the app is in English and loads none"),
    ("plugins/imageformats/qpdf.dll", "reads PDFs as images; the app never does"),
    ("Qt6Pdf.dll", "only the PDF image plugin above uses it"),
    ("plugins/platforminputcontexts", "the on-screen keyboard input plugin"),
    ("Qt6VirtualKeyboard.dll", "only the on-screen keyboard plugin uses it"),
    ("Qt6Quick.dll", "Qt Quick, for the on-screen keyboard; the app has no QML"),
    ("Qt6Qml.dll", "QML, as above"),
    ("Qt6QmlModels.dll", "QML, as above"),
    ("Qt6QmlMeta.dll", "QML, as above"),
    ("Qt6QmlWorkerScript.dll", "QML, as above"),
    ("resources/*.debug.pak", "Qt WebEngine debug resources, should WebEngine ever come back in"),
    ("resources/*.debug.bin", "as above"),
]


def targets():
    for pattern, why in UNUSED:
        for path in glob.glob(os.path.join(QT, pattern)):
            yield path, why


def binaries(skip):
    for dp, dn, fn in os.walk(APP):
        for f in fn:
            path = os.path.join(dp, f)
            if f.lower().endswith((".dll", ".pyd", ".exe")) and not any(
                    os.path.normcase(path).startswith(os.path.normcase(s)) for s in skip):
                yield path


def main():
    if not os.path.isdir(QT):
        print("build_trim: no PySide6 folder in %s -- nothing to do" % APP)
        return 0
    found = list(targets())
    removing = {os.path.basename(p).lower() for p, _ in found if p.lower().endswith(".dll")}
    for path, _ in found:
        if os.path.isdir(path):
            removing |= {f.lower() for f in os.listdir(path) if f.lower().endswith(".dll")}
    still_used = []
    for path in binaries([p for p, _ in found]):
        try:
            imports = {name.lower() for name, _resolved in bindepend.get_imports(path)}
        except Exception as exc:   # noqa: BLE001 -- an unreadable file is reported, not guessed about
            print("build_trim: couldn't read the imports of %s (%s)" % (path, exc))
            return 1
        for dll in imports & removing:
            still_used.append((os.path.relpath(path, APP), dll))
    if still_used:
        print("build_trim: NOT trimming -- these still link to a DLL on the list:")
        for user, dll in still_used:
            print("   %s -> %s" % (user, dll))
        return 1
    saved = 0
    for path, why in found:
        if os.path.isdir(path):
            size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(path) for f in fn)
            shutil.rmtree(path)
        else:
            size = os.path.getsize(path)
            os.remove(path)
        saved += size
        print("build_trim: removed %-34s %6.1f MB  (%s)" % (os.path.relpath(path, QT), size / 1048576, why))
    print("build_trim: %.1f MB of unused Qt left out" % (saved / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
