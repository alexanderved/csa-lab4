import argparse
from pathlib import Path

from src.translator.translator import translate

parser = argparse.ArgumentParser()

parser.add_argument("-i", "--source", help="Файл с исходным кодом", required=True)
parser.add_argument("-o", "--binary", help="Файл для машинного кода")
parser.add_argument("-d", "--debug", help="Файл для отладочной информации")
parser.add_argument("-p", "--path", help="Путь до директории с выходными файлами")

args = parser.parse_args()

source = args.source
binary = args.binary
debug = args.debug
path = args.path

if path is None:
    path = Path("./")
else:
    path = Path(path)

if not path.exists():
    path.mkdir()

source_path = Path(source)
if binary is None:
    binary = str(Path(path, source_path.name).with_suffix(".bin"))
if debug is None:
    debug = str(Path(path, source_path.name).with_suffix(".debug"))

translate(source, binary, debug)
