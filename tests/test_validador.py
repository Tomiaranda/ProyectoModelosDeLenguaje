"""Pruebas del validador de estud.ia (no usan red ni API keys).

    python -m pytest tests
"""
import copy

from estudia.validador import extraer_json, validar_banco

BANCO = {
    "titulo": "Prueba",
    "descripcion": "Banco de ejemplo",
    "preguntas": [
        {"tipo": "multiple_choice", "consigna": "¿2+2?", "opciones": ["3", "4", "5"],
         "correctas": [1], "explicacion": "Suma."},
        {"tipo": "seleccion_multiple", "consigna": "Pares", "opciones": ["1", "2", "4"],
         "correctas": [1, 2], "explicacion": "Divisibles por 2."},
        {"tipo": "verdadero_falso", "consigna": "El cielo es azul.", "respuesta": True,
         "explicacion": "Dispersión de Rayleigh."},
        {"tipo": "completar", "consigna": "La capital de Francia es ___.",
         "respuestas": [["París", "Paris"]], "explicacion": "Geografía."},
        {"tipo": "texto", "consigna": "Definí token.", "respuestas": ["Unidad de texto"],
         "explicacion": "Tokenización."},
        {"tipo": "unir", "consigna": "Uní", "pares": [["A", "1"], ["B", "2"]],
         "explicacion": "Correspondencia."},
        {"tipo": "ordenar", "consigna": "Ordená", "pasos": ["uno", "dos", "tres"],
         "explicacion": "Orden natural."},
    ],
}


def test_banco_valido():
    assert validar_banco(copy.deepcopy(BANCO), 7) == []


def test_cantidad_incorrecta():
    assert any("Se pidieron 10" in e for e in validar_banco(copy.deepcopy(BANCO), 10))


def test_multiple_choice_con_dos_correctas():
    banco = copy.deepcopy(BANCO)
    banco["preguntas"][0]["correctas"] = [0, 1]
    assert any("UN solo índice" in e for e in validar_banco(banco, 7))


def test_indice_fuera_de_rango():
    banco = copy.deepcopy(BANCO)
    banco["preguntas"][1]["correctas"] = [5]
    assert any("fuera de rango" in e for e in validar_banco(banco, 7))


def test_huecos_no_coinciden():
    banco = copy.deepcopy(BANCO)
    banco["preguntas"][3]["consigna"] = "___ y ___"
    assert any("2 huecos" in e for e in validar_banco(banco, 7))


def test_falta_explicacion():
    banco = copy.deepcopy(BANCO)
    del banco["preguntas"][4]["explicacion"]
    assert any("explicacion" in e for e in validar_banco(banco, 7))


def test_tipo_no_pedido():
    errores = validar_banco(copy.deepcopy(BANCO), 7, ["verdadero_falso"])
    assert any("no pidió" in e for e in errores)


def test_normaliza_descuidos():
    banco = copy.deepcopy(BANCO)
    banco["preguntas"][0]["correctas"] = 1          # índice suelto
    banco["preguntas"][2]["respuesta"] = "true"     # booleano entre comillas
    assert validar_banco(banco, 7) == []
    assert banco["preguntas"][0]["correctas"] == [1]
    assert banco["preguntas"][2]["respuesta"] is True


def test_preguntas_repetidas():
    banco = copy.deepcopy(BANCO)
    banco["preguntas"].append(copy.deepcopy(banco["preguntas"][0]))
    assert any("repetidas" in e for e in validar_banco(banco, 8))


def test_extraer_json_con_bloque_de_codigo():
    assert extraer_json('```json\n{"titulo": "x", "preguntas": []}\n```')["titulo"] == "x"


def test_repara_json_mal_formado():
    roto = ('```json\n{"titulo": "T", "preguntas": [\n'
            '  {"tipo": "verdadero_falso", "consigna": "El "transformer" usa atención", "respuesta": true}\n'
            '  {"tipo": "verdadero_falso", "consigna": "Otra", "respuesta": false}\n]}\n```')
    banco = extraer_json(roto)
    assert len(banco["preguntas"]) == 2
    assert "transformer" in banco["preguntas"][0]["consigna"]
