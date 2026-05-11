"""i18n locale detection + label loading."""

from redrobin.tui.i18n import Labels, detect_locale, load_labels


def test_detect_locale_from_LANG_es(monkeypatch):
    monkeypatch.setenv("LANG", "es_CR.UTF-8")
    monkeypatch.delenv("LANGUAGE", raising=False)
    assert detect_locale() == "es"


def test_detect_locale_from_LANG_en(monkeypatch):
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("LANGUAGE", raising=False)
    assert detect_locale() == "en"


def test_detect_locale_fallback_for_unknown(monkeypatch):
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.delenv("LANGUAGE", raising=False)
    assert detect_locale() == "en"


def test_detect_locale_fallback_when_no_env(monkeypatch):
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LANGUAGE", raising=False)
    assert detect_locale() == "en"


def test_load_labels_en_has_required_sections():
    labels = load_labels("en")
    assert "tui" in labels
    assert "verdict" in labels
    assert "help" in labels
    assert labels["tui"]["working_memory"] == "WORKING MEMORY"


def test_load_labels_es_has_spanish_terms():
    labels = load_labels("es")
    assert labels["tui"]["working_memory"] == "MEMORIA DE TRABAJO"
    assert labels["verdict"]["danger"] == "PELIGRO"


def test_load_labels_unknown_locale_falls_back_to_en():
    labels = load_labels("fr")
    assert labels["tui"]["working_memory"] == "WORKING MEMORY"


def test_labels_dotted_access():
    labels = Labels(load_labels("en"))
    assert labels.tui.working_memory == "WORKING MEMORY"
    assert labels.verdict.danger == "DANGER"
    # Missing key returns the key itself (graceful fallback)
    assert labels.tui.nonexistent == "nonexistent"
