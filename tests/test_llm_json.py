from unittest import mock

import pytest

from src.llm_json import LLMJsonError, invoke_json, parse_json


@pytest.mark.parametrize("texte", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Voici le résultat :\n{"a": 1}\nBonne journée',
])
def test_parse_json_extrait_l_objet(texte):
    assert parse_json(texte) == {"a": 1}


def test_parse_json_tableau():
    assert parse_json('Résultat : [{"a": 1}, {"a": 2}]') == [{"a": 1}, {"a": 2}]


def test_parse_json_refuse_le_texte_libre():
    with pytest.raises(ValueError):
        parse_json("Je ne peux pas répondre.")


def _llm(*reponses):
    llm = mock.Mock()
    llm.invoke.side_effect = [mock.Mock(content=r) for r in reponses]
    return llm


def test_invoke_json_relance_si_la_premiere_reponse_est_invalide():
    llm = _llm("oups", '{"ok": true}')
    data, _ = invoke_json(llm, [], expected=dict)
    assert data == {"ok": True}
    assert llm.invoke.call_count == 2


def test_invoke_json_echoue_explicitement():
    llm = _llm("oups", "toujours pas")
    with pytest.raises(LLMJsonError):
        invoke_json(llm, [], expected=dict, attempts=2)


def test_invoke_json_encapsule_un_objet_si_une_liste_est_attendue():
    data, _ = invoke_json(_llm('{"nom": "Alice"}'), [], expected=list)
    assert data == [{"nom": "Alice"}]


def test_invoke_json_refuse_le_mauvais_type():
    with pytest.raises(LLMJsonError):
        invoke_json(_llm("[1, 2]", "[3]"), [], expected=dict)
