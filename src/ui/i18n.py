"""Loading and selecting UI translations for VRD Next.

Translations live in the ``translations`` folder beside the source, as Qt
translation files: an editable ``.ts`` (the English template, and one per
language) and a compiled ``.qm`` that the application actually loads.  English
is the built-in default and needs no file.

Adding a language is a three-step job (see ``translations/README.md``):
  1. translate the ``.ts`` template - by hand in Qt Linguist, or by handing the
     XML to a translator/chatbot and asking it to fill in the ``<translation>``
     tags;
  2. compile it to a ``.qm`` with ``pyside6-lrelease`` (a one-line helper does
     this for every file at once);
  3. drop the ``.qm`` in the folder - it then appears in the Language dropdown
     under Settings, chosen by its code.
"""

import os

from PySide6.QtCore import QTranslator

_APP = "vrd-next"

# Friendly names for the languages we anticipate, so the dropdown reads nicely.
# A .qm whose code isn't listed here still appears, labelled by its code.
LANGUAGE_NAMES = {
    "en": "English",
    "de": "Deutsch (German)",
    "fr": "Français (French)",
    "es": "Español (Spanish)",
    "it": "Italiano (Italian)",
    "nl": "Nederlands (Dutch)",
    "pt": "Português (Portuguese)",
    "pl": "Polski (Polish)",
    "sv": "Svenska (Swedish)",
}


def translations_dir():
    """The folder holding the .ts/.qm files (…/src/translations)."""
    return os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "translations"
    )


def _code_from_qm(filename):
    """'vrd-next_de.qm' -> 'de', or None if it isn't one of ours."""
    base = os.path.basename(filename)
    prefix = _APP + "_"
    if base.startswith(prefix) and base.endswith(".qm"):
        return base[len(prefix):-3]
    return None


def available_languages():
    """Return ``[(code, display_name), …]`` - English first, then every language
    that has a compiled ``.qm`` in the translations folder, sorted by name."""
    languages = [("en", LANGUAGE_NAMES["en"])]
    folder = translations_dir()
    found = []
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            code = _code_from_qm(name)
            if code and code != "en":
                found.append((code, LANGUAGE_NAMES.get(code, code)))
    found.sort(key=lambda pair: pair[1].lower())
    return languages + found


# Hold references so the installed translators aren't garbage-collected.
_translator = None
_qt_translator = None


def install_language(app, code):
    """Install the translation for ``code`` on ``app``.

    English - or any unknown/missing code - removes any translation, leaving the
    built-in English strings.  Returns the code actually applied, so the caller
    can tell when a requested language wasn't available.  Qt's own translations
    (qtbase_<code>.qm) are loaded too, so standard widgets - the OK/Cancel/Save
    buttons, file dialogs and the like - are translated without us having to
    wrap them.
    """
    global _translator, _qt_translator
    for existing in (_translator, _qt_translator):
        if existing is not None:
            app.removeTranslator(existing)
    _translator = None
    _qt_translator = None
    if not code or code == "en":
        return "en"
    qm = os.path.join(translations_dir(), "%s_%s.qm" % (_APP, code))
    if not os.path.exists(qm):
        return "en"
    translator = QTranslator(app)
    if not translator.load(qm):
        return "en"
    app.installTranslator(translator)
    _translator = translator
    # Qt's built-in translations for standard widgets/dialogs.
    try:
        from PySide6.QtCore import QLibraryInfo
        qt_dir = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
        qt_tr = QTranslator(app)
        if qt_tr.load("qtbase_%s" % code, qt_dir):
            app.installTranslator(qt_tr)
            _qt_translator = qt_tr
    except Exception:
        pass
    return code
