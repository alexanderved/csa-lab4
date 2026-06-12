import contextlib
import io
import logging
import os
import tempfile

import pytest
from src.translator import translate
from src.simulator import simulate

MAX_LOG = 10000


@pytest.mark.golden_test("golden/*.yaml")
def test_translator_and_machine(golden, caplog):
    caplog.set_level(logging.DEBUG)

    with tempfile.TemporaryDirectory() as tmpdirname:
        source = os.path.join(tmpdirname, "source.lisp")
        config = os.path.join(tmpdirname, "config.yaml")
        binary = os.path.join(tmpdirname, "output.bin")
        debug = os.path.join(tmpdirname, "output.debug")

        with open(source, "w", encoding="utf-8") as file:
            file.write(golden["source"])
        with open(config, "w", encoding="utf-8") as file:
            file.write(golden["config"])

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            translate(source, binary, debug)
            print("============================================================")
            simulate(binary, config)

        with open(binary, "rb") as file:
            code = file.read().hex(" ").upper()
        with open(debug, encoding="utf-8") as file:
            code_hex = file.read()

        log = caplog.text.split("\n")
        if len(log) > MAX_LOG:
            log = log[: MAX_LOG // 2] + ["..."] + log[-MAX_LOG // 2 :]
        log = "\n".join(log)

        assert code == golden.out["binary"]
        assert code_hex == golden.out["debug"]
        assert stdout.getvalue() == golden.out["stdout"]
        assert log == golden.out["log"]

        """ for i, (l1, l2) in enumerate(zip(log, golden.out["log"].split("\n"))):
            # print(l1, l2)
            assert l1 == l2 """
