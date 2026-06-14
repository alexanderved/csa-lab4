import math
from dataclasses import dataclass
from enum import Enum, auto

WORD_SIZE = 4
VECTOR_SIZE = 4


@dataclass(frozen=True)
class Symbol:
    value: str


class Opcode(Enum):
    NOP = 0
    HALT = auto()

    # Операции работы с памятью
    LD = auto()
    ST = auto()

    # Арифметические операции
    ADD = auto()
    ADC = auto()
    SUB = auto()
    CMP = auto()

    MUL = auto()
    DIV = auto()
    REM = auto()

    # Битовые операции
    SHL = auto()
    SHR = auto()

    AND = auto()
    OR = auto()
    XOR = auto()
    NOT = auto()

    # Стековые операции
    PUSH = auto()
    POP = auto()

    # Операции управления
    JMP = auto()

    JEQ = auto()
    JNE = auto()

    JLT = auto()
    JLE = auto()

    JGT = auto()
    JGE = auto()

    CALL = auto()
    RET = auto()
    IRET = auto()

    # Операции ввода-вывода
    IN = auto()
    OUT = auto()

    # Векторные операции работы с памятью
    VLD = auto()
    VST = auto()

    # Векторные арфиметические операции
    VADD = auto()
    VSUB = auto()
    VMUL = auto()
    VDIV = auto()

    # Векторные стековые операции
    VPUSH = auto()
    VPOP = auto()

    # Векторные операции с масками
    VMNOT = auto()

    VMEQ = auto()
    VMNE = auto()

    VMLT = auto()
    VMLE = auto()

    VMGT = auto()
    VMGE = auto()

    VMLD = auto()
    VMST = auto()

    def size(self):
        if self in ADDRESSLESS_INSTRUCTIONS:
            return ADDRESSLESS_INSTRUCTION_SIZE
        else:
            return ADDRESS_INSTRUCTION_SIZE


ADDRESSLESS_INSTRUCTIONS = {
    Opcode.NOP,
    Opcode.HALT,
    Opcode.NOT,
    Opcode.PUSH,
    Opcode.POP,
    Opcode.RET,
    Opcode.IRET,
    Opcode.VPUSH,
    Opcode.VPOP,
    Opcode.VMNOT,
}

CONTROL_INSTRUCTIONS = {
    Opcode.HALT,
    Opcode.JMP,
    Opcode.JEQ,
    Opcode.JNE,
    Opcode.JLT,
    Opcode.JLE,
    Opcode.JGT,
    Opcode.JGE,
    Opcode.CALL,
    Opcode.RET,
    Opcode.IRET,
}

VECTOR_INSTRUCTIONS = {
    Opcode.VLD,
    Opcode.VST,
    Opcode.VADD,
    Opcode.VSUB,
    Opcode.VMUL,
    Opcode.VDIV,
    Opcode.VPUSH,
    Opcode.VPOP,
    Opcode.VMNOT,
    Opcode.VMEQ,
    Opcode.VMNE,
    Opcode.VMLT,
    Opcode.VMLE,
    Opcode.VMGT,
    Opcode.VMGE,
    Opcode.VMLD,
    Opcode.VMST,
}

MASKED_INSTRUCTIONS = {
    Opcode.VMLD,
    Opcode.VMST,
}

MASK_GEN_INSTRUCTIONS = {
    Opcode.VMNOT,
    Opcode.VMEQ,
    Opcode.VMNE,
    Opcode.VMLT,
    Opcode.VMLE,
    Opcode.VMGT,
    Opcode.VMGE,
}

ADDRESSLESS_INSTRUCTION_SIZE = 1
ADDRESS_INSTRUCTION_SIZE = 5


class AddressMode(Enum):
    IMM = 0
    ADDR = 1
    SP = 2
    SP_IND = 3


@dataclass
class Instruction:
    opcode: Opcode
    addr_mode: AddressMode | None = None
    operand: int | Symbol | None = None


def to_little_endian(value: int):
    bit_len = 1
    if value >= 0:
        bit_len = value.bit_length() + 1
    else:
        mag = -value
        if mag & (mag - 1) == 0:
            bit_len = mag.bit_length()
        else:
            bit_len = mag.bit_length() + 1
        bit_len = max(2, bit_len)
    byte_len = math.ceil(bit_len / 8) or 1

    le = value.to_bytes(byte_len, "little", signed=True)
    if len(le) < 4:
        if le[-1] < (1 << 7):
            return le + bytes([0] * (4 - len(le)))
        else:
            return le + bytes([0xFF] * (4 - len(le)))
    else:
        return le[0:4]


def from_little_endian(le: bytes):
    return int.from_bytes(le, "little", signed=True)


def hex_little_endian(le: bytes):
    return list(map(lambda b: f"{dec_to_hex(b):>02}", le))


def bin_to_hex(binary: str):
    return f"{int(binary, 2):>02X}"


def dec_to_bin(dec: int):
    return bin(dec).replace("0b", "")


def dec_to_hex(dec: int):
    return f"{dec:>02X}"