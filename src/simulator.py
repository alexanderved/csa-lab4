from __future__ import annotations

import argparse
import ast
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Literal, TextIO

import logging
import yaml
from src.common import (
    ADDRESS_INSTRUCTION_SIZE,
    ADDRESSLESS_INSTRUCTIONS,
    CONTROL_INSTRUCTIONS,
    MASK_GEN_INSTRUCTIONS,
    MASKED_INSTRUCTIONS,
    VECTOR_INSTRUCTIONS,
    VECTOR_SIZE,
    WORD_SIZE,
    AddressMode,
    Opcode,
    from_little_endian,
    to_little_endian,
)


logger = logging.getLogger()

START_ADDR = 0x0
INT_ADDR = ADDRESS_INSTRUCTION_SIZE


LOW_SIGNAL = 0
HIGH_SIGNAL = 1


SEL_PC_INT = 0
SEL_PC_PL4 = 1
SEL_PC_PL1 = 2
SEL_PC_JMP = 3

SEL_IN_INPUT_DEV = 0
SEL_IN_MEMORY = 1

SEL_SP_PL4 = 0
SEL_SP_MI4 = 1
SEL_SP_PL16 = 2
SEL_SP_MI16 = 3
SEL_SP_DATA = 4

SEL_AR_PC = 0
SEL_AR_PC_PL1 = 1
SEL_AR_NEXT_SP = 2
SEL_AR_SP = 3
SEL_AR_DATA = 4

SEL_ADDR_AR = 0
SEL_ADDR_PC = 1

SEL_EXT_FALSE = 0
SEL_EXT_TRUE = 1

SEL_AC_DATA = 0
SEL_AC_ALU_RES = 1

SEL_OUT_AC = 0
SEL_OUT_MASK = 1
SEL_OUT_FLAGS = 2
SEL_OUT_PC = 3


NF = 0
ZF = 1
VF = 2
CF = 3

ALU_OP_NOP = 0
ALU_OP_ADD = 1
ALU_OP_INC = 2
ALU_OP_SUB = 3

ALU_OP_MUL = 4
ALU_OP_DIV = 5
ALU_OP_REM = 6

ALU_OP_SHL = 7
ALU_OP_SHR = 8

ALU_OP_AND = 9
ALU_OP_OR = 10
ALU_OP_XOR = 11
ALU_OP_NOT = 12

ALU_OP_SET_FLAGS = 13

ALU_OP_SET_MASK = 14
ALU_OP_MASK_EQ = 15
ALU_OP_MASK_NE = 16
ALU_OP_MASK_LT = 17
ALU_OP_MASK_LE = 18
ALU_OP_MASK_GT = 19
ALU_OP_MASK_GE = 20
ALU_OP_MASK_NOT = 21


def mux(sel: int, *options):
    return options[sel]


def b2i(bits: list[int]):
    value = 0
    for i, b in enumerate(bits):
        value += b << i

    return value


def i2b(value: int, nb_bits: int = 4):
    bits = []
    for _ in range(value.bit_length()):
        bits.append(value & 1)
        value >>= 1

    if len(bits) < nb_bits:
        bits += [0] * (nb_bits - len(bits))

    return bits[:nb_bits]


def s2v(scalar: int, fill: int = 0):
    return [scalar] + [fill] * (VECTOR_SIZE - 1)


def ext(scalar: int):
    return [scalar] * VECTOR_SIZE


def extract_instr(data: list[int]):
    value = to_little_endian(data[0])[0]

    opcode = Opcode(value >> 2)
    addr_mode = AddressMode(value & 0b11)

    if opcode in ADDRESSLESS_INSTRUCTIONS:
        addr_mode = None

    return opcode, addr_mode


def log(s: str):
    logger.debug(s)


class Config:
    limit: int
    memory_size: int
    tick_duration_ms: int | None
    data: str

    def __init__(self, limit, memory_size, tick_duration_ms, data):
        self.limit = limit
        self.memory_size = memory_size
        self.tick_duration_ms = tick_duration_ms
        self.data = data

    @staticmethod
    def from_file(config: TextIO):
        content = yaml.safe_load(config)

        limit = 1000
        if "limit" in content:
            limit = content["limit"]

        memory_size = 0x1000
        if "memory_size" in content:
            memory_size = content["memory_size"]

        tick_duration_ms = None
        if "tick_duration_ms" in content:
            tick_duration_ms = content["tick_duration_ms"]

        data = "[]"
        if "data" in content:
            data = content["data"]

        return Config(limit, memory_size, tick_duration_ms, data)


@dataclass
class SignalContext:
    intrq: int = LOW_SIGNAL

    latch_pc: int = LOW_SIGNAL
    sel_pc: int = 0
    set_ei: int = LOW_SIGNAL
    reset_ei: int = LOW_SIGNAL
    latch_port: int = LOW_SIGNAL

    rd_in: int = LOW_SIGNAL
    wr_out: int = LOW_SIGNAL
    rd_mem: list[int] = field(default_factory=lambda: ext(LOW_SIGNAL))
    wr_mem: list[int] = field(default_factory=lambda: ext(LOW_SIGNAL))

    latch_ir: int = LOW_SIGNAL
    latch_sp: int = LOW_SIGNAL
    latch_ar: int = LOW_SIGNAL
    latch_ac: list[int] = field(default_factory=lambda: ext(LOW_SIGNAL))
    latch_flags: int = LOW_SIGNAL
    latch_mask: int = LOW_SIGNAL

    sel_sp: int = 0
    sel_ar: int = 0
    sel_ac: int = 0
    sel_in: int = 0
    sel_out: int = 0
    sel_ext: int = 0
    sel_addr: int = 0

    op: int = 0

    def __or__(self, other: SignalContext):
        return SignalContext(
            intrq=self.intrq | other.intrq,
            latch_pc=self.latch_pc | other.latch_pc,
            sel_pc=self.sel_pc | other.sel_pc,
            set_ei=self.set_ei | other.set_ei,
            reset_ei=self.reset_ei | other.reset_ei,
            latch_port=self.latch_port | other.latch_port,
            rd_in=self.rd_in | other.rd_in,
            wr_out=self.wr_out | other.wr_out,
            rd_mem=[self.rd_mem[i] | other.rd_mem[i] for i in range(VECTOR_SIZE)],
            wr_mem=[self.wr_mem[i] | other.wr_mem[i] for i in range(VECTOR_SIZE)],
            latch_ir=self.latch_ir | other.latch_ir,
            latch_sp=self.latch_sp | other.latch_sp,
            latch_ar=self.latch_ar | other.latch_ar,
            latch_ac=[self.latch_ac[i] | other.latch_ac[i] for i in range(VECTOR_SIZE)],
            latch_flags=self.latch_flags | other.latch_flags,
            latch_mask=self.latch_mask | other.latch_mask,
            sel_sp=self.sel_sp | other.sel_sp,
            sel_ar=self.sel_ar | other.sel_ar,
            sel_ac=self.sel_ac | other.sel_ac,
            sel_in=self.sel_in | other.sel_in,
            sel_out=self.sel_out | other.sel_out,
            sel_ext=self.sel_ext | other.sel_ext,
            sel_addr=self.sel_addr | other.sel_addr,
            op=self.op | other.op,
        )


class TickGenerator:
    tick: int

    def __init__(self):
        self.tick = 0

    def next_tick(self):
        self.tick += 1


class Memory:
    content: bytearray

    def __init__(self, memory_size: int, code: bytearray):
        self.content = code + bytearray(memory_size - len(code))

    def read_word(self, addr: int):
        if addr > len(self.content):
            raise ValueError("Несуществующий адрес памяти")

        return from_little_endian(self.content[addr : addr + VECTOR_SIZE])

    def write_word(self, addr: int, value: int):
        if addr > len(self.content):
            raise ValueError("Несуществующий адрес памяти")

        self.content[addr : addr + VECTOR_SIZE] = to_little_endian(value)

    def read(self, addr: int, mask: list[int]):
        return [self.read_word(addr + WORD_SIZE * i) if mask[i] == HIGH_SIGNAL else 0 for i in range(VECTOR_SIZE)]

    def write(self, addr: int, value: list[int], mask: list[int]):
        for i in range(VECTOR_SIZE):
            if mask[i] == HIGH_SIGNAL:
                self.write_word(addr + WORD_SIZE * i, value[i])


def to_s32(value: int):
    mask = (1 << 31) - 1
    return (value & mask) - (value & (mask + 1))


def to_u32(value: int):
    mask = (1 << 32) - 1
    return value & mask


class InputDevice:
    tg: TickGenerator
    queue: list[(int, str | int)]
    idx: int

    def __init__(self, tg, queue):
        self.tg = tg
        self.queue = queue
        self.idx = 0

    def send_intrq(self, signals: SignalContext):
        if self.idx < len(self.queue) and self.queue[self.idx][0] <= self.tg.tick:
            signals.intrq = HIGH_SIGNAL

    def read(self):
        if self.idx >= len(self.queue):
            raise ValueError("Конец ввода")

        t, value = self.queue[self.idx]
        if t > self.tg.tick:
            raise ValueError("Запрос к устройству без сигнала готовности")

        self.idx += 1

        if type(value) is int:
            return value
        elif type(value) is str and len(value) == 1:
            return ord(value)
        else:
            raise ValueError("Неизвестный тип входных данных")


class OutputDevice:
    data_type: Literal["string", "array"]
    queue: list[str | int]

    def __init__(self, data_type):
        self.data_type = data_type
        self.queue = []

    def write(self, value: int):
        if self.data_type == "array":
            self.queue.append(value)
        else:
            self.queue.append(chr(value))


def debug_info_template(op: str, cu: ControlUnit, mark_op_start: bool = False):
    nzvc_str = f"{b2i(cu.dp.flags):04b}"[::-1]
    mask_str = f"{b2i(cu.dp.mask):04b}"[::-1]

    tick_str = f" {'*' if mark_op_start else ' '} Tick {cu.tg.tick}"
    op_str = f"[ISR] {op}" if cu.is_isr else op
    register_str = (
        f"{f'IR={cu.dp.ir_opcode.name},':<10} {f'PC=0x{cu.pc:X},':<10} {f'AR=0x{cu.dp.ar:X},':<10} "
        + f"{f'SP=0x{cu.dp.sp:X},':<10} NZVC={nzvc_str}, MASK={mask_str}, {f'AC={cu.dp.ac}'}"
    )
    stack_str = (
        f"Stack={[cu.dp.memory.read_word(addr) for addr in range(cu.dp.sp, len(cu.dp.memory.content), WORD_SIZE)]}"
    )
    output_str = f"Output={cu.dp.out_devs[1].queue}, {cu.dp.out_devs[2].queue}"

    return f"{tick_str:<13} | {op_str:<50} | {register_str:<85} | {stack_str} | {output_str}"


class Cycle:
    signals: list[SignalContext | Callable[..., SignalContext]]
    transitions: list[Callable[..., int] | None]
    debug_info: list[Callable[[ControlUnit, SignalContext], str] | str | None]
    is_op_start: bool

    def __init__(self, signals, transitions=None, debug_info=None, is_op_start=False):
        self.signals = signals

        if transitions is None:
            self.transitions = [None] * len(self.signals)
        else:
            self.transitions = transitions

        if debug_info is None:
            self.debug_info = [None] * len(self.signals)
        else:
            self.debug_info = debug_info

        self.is_op_start = is_op_start

    def transition(self, step: int, *args):
        t = None
        if step < len(self.transitions):
            t = self.transitions[step]

        if t is None:
            return (step + 1) % len(self.signals)

        return t(step, *args)

    def debug(self, step: int, cu: ControlUnit, signals: SignalContext):
        d = None
        if step < len(self.debug_info):
            d = self.debug_info[step]

        if d is None:
            return debug_info_template("NOP", cu)

        if type(d) is not str:
            d = d(cu, signals)

        return debug_info_template(d, cu, self.is_op_start and step == 0)


FETCH_INSTRUCTION_CYCLE = Cycle(
    signals=[
        SignalContext(
            sel_addr=SEL_ADDR_PC,
            rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
            sel_in=SEL_IN_MEMORY,
            sel_ext=SEL_EXT_FALSE,
            latch_ir=HIGH_SIGNAL,
        ),
    ],
    debug_info=[
        "FETCH INSTR: IR <- MEM[PC]",
    ],
    is_op_start=True,
)

FETCH_ADDRESS_CYCLE = Cycle(
    signals=[
        SignalContext(
            sel_pc=SEL_PC_PL1,
            latch_pc=HIGH_SIGNAL,
            sel_ar=SEL_AR_PC_PL1,
            latch_ar=HIGH_SIGNAL,
        ),
        lambda addr_mode: SignalContext(
            sel_addr=SEL_ADDR_AR,
            rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
            sel_in=SEL_IN_MEMORY,
            sel_ext=SEL_EXT_FALSE,
            sel_sp=SEL_SP_DATA if addr_mode not in {AddressMode.IMM, AddressMode.ADDR} else 0,
            sel_ar=SEL_AR_DATA if addr_mode == AddressMode.ADDR else SEL_AR_NEXT_SP,
            latch_ar=HIGH_SIGNAL,
        ),
        SignalContext(
            sel_addr=SEL_ADDR_AR,
            rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
            sel_in=SEL_IN_MEMORY,
            sel_ext=SEL_EXT_FALSE,
            sel_ar=SEL_AR_DATA,
            latch_ar=HIGH_SIGNAL,
        ),
    ],
    transitions=[
        lambda step, addr_mode: 0 if addr_mode == AddressMode.IMM else step + 1,
        lambda step, addr_mode: 0 if addr_mode != AddressMode.SP_IND else step + 1,
    ],
    debug_info=[
        "FETCH ADDR: PC, AR <- PC + 1",
        lambda cu, _: (
            f"FETCH ADDR: AR <- {'' if cu.dp.ir_addr_mode == AddressMode.ADDR else 'SP +'} {cu.dp.memory.read_word(cu.dp.ar)}"
        ),
        "FETCH ADDR INDIRECT: AR <- MEM[AR]",
    ],
)

INTERRUPT_CYCLE = Cycle(
    signals=[
        lambda op_size, do_jump, intrq: SignalContext(
            sel_addr=SEL_ADDR_AR if do_jump else 0,
            rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL) if do_jump else ext(0),
            sel_in=SEL_IN_MEMORY if do_jump else 0,
            sel_ext=SEL_EXT_FALSE if do_jump else 0,
            sel_pc=SEL_PC_JMP if do_jump else (SEL_PC_PL1 if op_size == 1 else SEL_PC_PL4),
            latch_pc=HIGH_SIGNAL,
            sel_sp=SEL_SP_MI4 if intrq else 0,
            latch_sp=HIGH_SIGNAL if intrq else LOW_SIGNAL,
            sel_ar=SEL_AR_NEXT_SP if intrq else 0,
            latch_ar=HIGH_SIGNAL if intrq else LOW_SIGNAL,
        ),
        SignalContext(
            sel_out=SEL_OUT_PC,
            sel_addr=SEL_ADDR_AR,
            wr_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
            sel_sp=SEL_SP_MI4,
            latch_sp=HIGH_SIGNAL,
            sel_ar=SEL_AR_NEXT_SP,
            latch_ar=HIGH_SIGNAL,
        ),
        SignalContext(
            sel_out=SEL_OUT_MASK,
            sel_addr=SEL_ADDR_AR,
            wr_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
            sel_sp=SEL_SP_MI4,
            latch_sp=HIGH_SIGNAL,
            sel_ar=SEL_AR_NEXT_SP,
            latch_ar=HIGH_SIGNAL,
        ),
        SignalContext(
            sel_out=SEL_OUT_FLAGS,
            sel_addr=SEL_ADDR_AR,
            wr_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
            sel_sp=SEL_SP_MI16,
            latch_sp=HIGH_SIGNAL,
            sel_ar=SEL_AR_NEXT_SP,
            latch_ar=HIGH_SIGNAL,
        ),
        SignalContext(
            sel_out=SEL_OUT_AC,
            sel_addr=SEL_ADDR_AR,
            wr_mem=ext(HIGH_SIGNAL),
            sel_pc=SEL_PC_INT,
            latch_pc=HIGH_SIGNAL,
            reset_ei=HIGH_SIGNAL,
        ),
    ],
    transitions=[
        lambda step, intrq: 0 if intrq == LOW_SIGNAL else step + 1,
    ],
    debug_info=[
        lambda cu, s: (
            f"INT: PC <- {mux(s.sel_pc, '', 'PC + 4', 'PC + 1', f'0x{cu.dp.memory.read_word(cu.dp.ar):X}')}"
            + (f", AR, SP <- SP - 4" if cu.ei & s.intrq else "")
        ),
        "INT: MEM[SP] <- PC, AR, SP <- SP - 4",
        "INT: MEM[SP] <- MASK, AR, SP <- SP - 4",
        "INT: MEM[SP] <- NZVC, AR, SP <- SP - 16",
        "INT: MEM[SP] <- AC, PC <- 0x5",
    ],
)

CALL_CYCLE = Cycle(
    signals=[
        SignalContext(
            sel_sp=SEL_SP_MI4,
            latch_sp=HIGH_SIGNAL,
            sel_ar=SEL_AR_NEXT_SP,
            latch_ar=HIGH_SIGNAL,
        ),
        SignalContext(
            sel_out=SEL_OUT_PC,
            sel_addr=SEL_ADDR_AR,
            wr_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
            sel_ar=SEL_AR_PC,
            latch_ar=HIGH_SIGNAL,
        ),
    ]
)

RET_CYCLE = Cycle(
    signals=[
        SignalContext(
            sel_sp=SEL_SP_PL4,
            latch_sp=HIGH_SIGNAL,
            sel_ar=SEL_AR_SP,
            latch_ar=HIGH_SIGNAL,
        ),
        SignalContext(
            sel_addr=SEL_ADDR_AR,
            rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
            sel_in=SEL_IN_MEMORY,
            sel_ext=SEL_EXT_FALSE,
            sel_pc=SEL_PC_JMP,
            latch_pc=HIGH_SIGNAL,
        ),
    ]
)

IRET_CYCLE = Cycle(
    signals=[
        SignalContext(
            sel_sp=SEL_SP_PL16,
            latch_sp=HIGH_SIGNAL,
            sel_ar=SEL_AR_SP,
            latch_ar=HIGH_SIGNAL,
        ),
        SignalContext(
            sel_addr=SEL_ADDR_AR,
            rd_mem=ext(HIGH_SIGNAL),
            sel_in=SEL_IN_MEMORY,
            sel_ext=SEL_EXT_FALSE,
            sel_ac=SEL_AC_DATA,
            latch_ac=ext(HIGH_SIGNAL),
            sel_sp=SEL_SP_PL4,
            latch_sp=HIGH_SIGNAL,
            sel_ar=SEL_AR_SP,
            latch_ar=HIGH_SIGNAL,
        ),
        SignalContext(
            sel_addr=SEL_ADDR_AR,
            rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
            sel_in=SEL_IN_MEMORY,
            sel_ext=SEL_EXT_FALSE,
            op=ALU_OP_SET_FLAGS,
            latch_flags=HIGH_SIGNAL,
            sel_sp=SEL_SP_PL4,
            latch_sp=HIGH_SIGNAL,
            sel_ar=SEL_AR_SP,
            latch_ar=HIGH_SIGNAL,
        ),
        SignalContext(
            set_ei=HIGH_SIGNAL,
            sel_addr=SEL_ADDR_AR,
            rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
            sel_in=SEL_IN_MEMORY,
            sel_ext=SEL_EXT_FALSE,
            op=ALU_OP_SET_MASK,
            latch_mask=HIGH_SIGNAL,
            sel_sp=SEL_SP_PL4,
            latch_sp=HIGH_SIGNAL,
            sel_ar=SEL_AR_SP,
            latch_ar=HIGH_SIGNAL,
        ),
    ]
)


class Stage(Enum):
    FETCH_INSTR = 0
    FETCH_ADDR = auto()
    EXECUTE = auto()
    INTERRUPT = auto()
    HALT = auto()


class ControlUnit:
    tg: TickGenerator
    in_dev: InputDevice
    dp: DataPath

    ei: int
    port: int
    pc: int

    stage: Stage
    cycle_step: int

    op_size: int
    do_jump: bool

    is_isr: bool

    def __init__(self, tg, in_dev, dp):
        self.tg = tg
        self.in_dev = in_dev
        self.dp = dp

        self.ei = 1
        self.pc = START_ADDR

        self.stage = Stage.FETCH_INSTR
        self.cycle_step = 0

        self.is_isr = False

    def merge_signals(self, signals, new_signals):
        signals.__dict__.update((signals | new_signals).__dict__)

    def log_debug_execute_cycle(self, opcode: Opcode):
        if opcode in ADDRESSLESS_INSTRUCTIONS or self.cycle_step != 0:
            log(debug_info_template(opcode.name, self))
        else:
            data = (
                self.dp.memory.read_word(self.dp.ar)
                if opcode not in VECTOR_INSTRUCTIONS
                else self.dp.memory.read(self.dp.ar, ext(1))
            )
            log(debug_info_template(f"{opcode.name} {data} @ 0x{self.dp.ar:X}", self))

    def perform_cycle(
        self,
        cycle: Cycle,
        signals: SignalContext,
        exec_args: list[Any] | None = None,
        transition_args: list[Any] | None = None,
        print_debug: bool = True,
    ):
        if exec_args is None:
            exec_args = []
        if transition_args is None:
            transition_args = []

        new_signals = cycle.signals[self.cycle_step]
        if type(new_signals) is not SignalContext:
            new_signals = new_signals(*exec_args)

        self.merge_signals(signals, new_signals)
        if print_debug:
            log(cycle.debug(self.cycle_step, self, signals))
        self.cycle_step = cycle.transition(self.cycle_step, *transition_args)

    def perform_execute_operation_cycle(self, signals: SignalContext):  # noqa C901
        applied_mask = s2v(HIGH_SIGNAL, LOW_SIGNAL)
        if self.dp.ir_opcode in MASKED_INSTRUCTIONS:
            applied_mask = self.dp.mask
        elif self.dp.ir_opcode in VECTOR_INSTRUCTIONS:
            applied_mask = ext(HIGH_SIGNAL)

        general_binary_ops = {
            Opcode.ADD: ALU_OP_ADD,
            Opcode.SUB: ALU_OP_SUB,
            Opcode.CMP: ALU_OP_SUB,
            Opcode.MUL: ALU_OP_MUL,
            Opcode.DIV: ALU_OP_DIV,
            Opcode.REM: ALU_OP_REM,
            Opcode.SHL: ALU_OP_SHL,
            Opcode.SHR: ALU_OP_SHR,
            Opcode.AND: ALU_OP_AND,
            Opcode.OR: ALU_OP_OR,
            Opcode.XOR: ALU_OP_XOR,
            Opcode.VADD: ALU_OP_ADD,
            Opcode.VSUB: ALU_OP_SUB,
            Opcode.VMUL: ALU_OP_MUL,
            Opcode.VDIV: ALU_OP_DIV,
            Opcode.VMEQ: ALU_OP_MASK_EQ,
            Opcode.VMNE: ALU_OP_MASK_NE,
            Opcode.VMLT: ALU_OP_MASK_LT,
            Opcode.VMLE: ALU_OP_MASK_LE,
            Opcode.VMGT: ALU_OP_MASK_GT,
            Opcode.VMGE: ALU_OP_MASK_GE,
        }

        opcode = self.dp.ir_opcode
        needs_ext = self.dp.ir_addr_mode == AddressMode.IMM and opcode in VECTOR_INSTRUCTIONS
        new_signals = SignalContext()

        self.log_debug_execute_cycle(opcode)

        match opcode:
            case Opcode.NOP:
                self.stage = Stage.INTERRUPT

            case Opcode.LD | Opcode.VLD | Opcode.VMLD:
                new_signals = SignalContext(
                    sel_addr=SEL_ADDR_AR,
                    rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL) if needs_ext else applied_mask,
                    sel_in=SEL_IN_MEMORY,
                    sel_ext=SEL_EXT_TRUE if needs_ext else SEL_EXT_FALSE,
                    sel_ac=SEL_AC_DATA,
                    latch_ac=applied_mask,
                )

            case Opcode.ST | Opcode.VST | Opcode.VMST:
                new_signals = SignalContext(
                    sel_out=SEL_OUT_AC,
                    sel_addr=SEL_ADDR_AR,
                    wr_mem=applied_mask,
                )

            case opcode if opcode in general_binary_ops:
                new_signals = SignalContext(
                    sel_addr=SEL_ADDR_AR,
                    rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL) if needs_ext else applied_mask,
                    sel_in=SEL_IN_MEMORY,
                    sel_ext=SEL_EXT_TRUE if needs_ext else SEL_EXT_FALSE,
                    op=general_binary_ops[opcode],
                    sel_ac=SEL_AC_ALU_RES,
                    latch_ac=applied_mask if opcode != Opcode.CMP else ext(0),
                    latch_flags=HIGH_SIGNAL,
                    latch_mask=HIGH_SIGNAL if opcode in MASK_GEN_INSTRUCTIONS else LOW_SIGNAL,
                )

            case Opcode.ADC:
                if self.cycle_step == 0:
                    new_signals = SignalContext(
                        sel_addr=SEL_ADDR_AR,
                        rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
                        sel_in=SEL_IN_MEMORY,
                        sel_ext=SEL_EXT_FALSE,
                        op=ALU_OP_ADD,
                        sel_ac=SEL_AC_ALU_RES,
                        latch_ac=s2v(HIGH_SIGNAL, LOW_SIGNAL),
                        latch_flags=HIGH_SIGNAL,
                    )

                    if self.dp.flags[CF] == 1:
                        self.cycle_step = 1
                else:
                    new_signals = SignalContext(
                        op=ALU_OP_INC,
                        sel_ac=SEL_AC_ALU_RES,
                        latch_ac=s2v(HIGH_SIGNAL, LOW_SIGNAL),
                        latch_flags=HIGH_SIGNAL,
                    )
                    self.cycle_step = 0

            case Opcode.NOT:
                new_signals = SignalContext(
                    op=ALU_OP_NOT,
                    sel_ac=SEL_AC_ALU_RES,
                    latch_ac=s2v(HIGH_SIGNAL),
                    latch_flags=HIGH_SIGNAL,
                )

            case Opcode.VMNOT:
                new_signals = SignalContext(
                    op=ALU_OP_MASK_NOT,
                    latch_mask=HIGH_SIGNAL,
                )

            case Opcode.PUSH | Opcode.VPUSH:
                if self.cycle_step == 0:
                    new_signals = SignalContext(
                        sel_sp=SEL_SP_MI16 if opcode == Opcode.VPUSH else SEL_SP_MI4,
                        latch_sp=HIGH_SIGNAL,
                        sel_ar=SEL_AR_NEXT_SP,
                        latch_ar=HIGH_SIGNAL,
                    )
                    self.cycle_step = 1
                else:
                    new_signals = SignalContext(
                        sel_out=SEL_OUT_AC,
                        sel_addr=SEL_ADDR_AR,
                        wr_mem=applied_mask,
                    )
                    self.cycle_step = 0

            case Opcode.POP | Opcode.VPOP:
                new_signals = SignalContext(
                    sel_sp=SEL_SP_PL16 if opcode == Opcode.VPOP else SEL_SP_PL4,
                    latch_sp=HIGH_SIGNAL,
                )

            case Opcode.IN:
                if self.cycle_step == 0:
                    new_signals = SignalContext(
                        sel_addr=SEL_ADDR_AR,
                        rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
                        sel_in=SEL_IN_MEMORY,
                        sel_ext=SEL_EXT_FALSE,
                        latch_port=HIGH_SIGNAL,
                    )
                    self.cycle_step = 1
                else:
                    new_signals = SignalContext(
                        rd_in=HIGH_SIGNAL,
                        sel_in=SEL_IN_INPUT_DEV,
                        sel_ext=SEL_EXT_FALSE,
                        sel_ac=SEL_AC_DATA,
                        latch_ac=s2v(HIGH_SIGNAL, LOW_SIGNAL),
                    )
                    self.cycle_step = 0

            case Opcode.OUT:
                if self.cycle_step == 0:
                    new_signals = SignalContext(
                        sel_addr=SEL_ADDR_AR,
                        rd_mem=s2v(HIGH_SIGNAL, LOW_SIGNAL),
                        sel_in=SEL_IN_MEMORY,
                        sel_ext=SEL_EXT_FALSE,
                        latch_port=HIGH_SIGNAL,
                    )
                    self.cycle_step = 1
                else:
                    new_signals = SignalContext(
                        wr_out=HIGH_SIGNAL,
                        sel_out=SEL_OUT_AC,
                    )
                    self.cycle_step = 0

            case _:
                assert False

        self.merge_signals(signals, new_signals)

    def perform_execute_control_cycle(self, signals: SignalContext):
        cond_jmp_map = {
            Opcode.JMP: lambda: True,
            Opcode.JEQ: lambda: self.dp.flags[ZF] == 1,
            Opcode.JNE: lambda: self.dp.flags[ZF] == 0,
            Opcode.JLT: lambda: self.dp.flags[NF] != self.dp.flags[VF],
            Opcode.JLE: lambda: self.dp.flags[NF] != self.dp.flags[VF] or self.dp.flags[ZF] == 1,
            Opcode.JGT: lambda: self.dp.flags[NF] == self.dp.flags[VF] and self.dp.flags[ZF] == 0,
            Opcode.JGE: lambda: self.dp.flags[NF] == self.dp.flags[VF],
        }

        opcode = self.dp.ir_opcode
        if opcode not in cond_jmp_map:
            self.log_debug_execute_cycle(opcode)

        op_size, do_jump = None, None
        match opcode:
            case Opcode.HALT:
                self.stage = Stage.HALT

            case opcode if opcode in cond_jmp_map:
                self.stage = Stage.INTERRUPT
                op_size, do_jump = opcode.size(), cond_jmp_map[opcode]()

            case Opcode.CALL:
                self.perform_cycle(CALL_CYCLE, signals, print_debug=False)

                if self.cycle_step == 0:
                    op_size, do_jump = None, True

            case Opcode.RET:
                self.perform_cycle(RET_CYCLE, signals, print_debug=False)

                if self.cycle_step == 0:
                    op_size, do_jump = WORD_SIZE, False

            case Opcode.IRET:
                self.perform_cycle(IRET_CYCLE, signals, print_debug=False)

                if self.cycle_step == 0:
                    op_size, do_jump = None, True

            case _:
                assert False

        return op_size, do_jump

    def perform_execute_cycle(self, signals: SignalContext):
        if self.dp.ir_opcode in CONTROL_INSTRUCTIONS:
            return self.perform_execute_control_cycle(signals)
        else:
            self.perform_execute_operation_cycle(signals)
            return self.dp.ir_opcode.size(), False

    def next_stage(self):
        if self.cycle_step != 0:
            return

        next_stage_map = {
            Stage.HALT: Stage.HALT,
            Stage.FETCH_INSTR: Stage.FETCH_ADDR,
            Stage.FETCH_ADDR: Stage.EXECUTE,
            Stage.EXECUTE: Stage.INTERRUPT,
            Stage.INTERRUPT: Stage.FETCH_INSTR,
        }

        self.stage = next_stage_map[self.stage]

    def simulate(self, signals: SignalContext):
        if self.stage == Stage.FETCH_INSTR:
            self.perform_cycle(FETCH_INSTRUCTION_CYCLE, signals)

        if self.stage == Stage.FETCH_ADDR:
            if self.dp.ir_addr_mode is not None:
                self.perform_cycle(FETCH_ADDRESS_CYCLE, signals, [self.dp.ir_addr_mode], [self.dp.ir_addr_mode])
            else:
                self.next_stage()

        print_int_debug = True
        intrq = signals.intrq & self.ei
        if self.stage == Stage.EXECUTE:
            self.op_size, self.do_jump = self.perform_execute_cycle(signals)
            if (
                self.stage == Stage.EXECUTE
                and signals.latch_pc == LOW_SIGNAL
                and not self.do_jump
                and intrq == LOW_SIGNAL
            ):
                print_int_debug = False
                self.next_stage()

        if self.stage == Stage.HALT:
            return

        if self.stage == Stage.INTERRUPT:
            self.perform_cycle(INTERRUPT_CYCLE, signals, [self.op_size, self.do_jump, intrq], [intrq], print_int_debug)

        self.dp.simulate(self, signals)

        if signals.latch_pc == HIGH_SIGNAL:
            self.pc = mux(signals.sel_pc, INT_ADDR, self.pc + 4, self.pc + 1, self.dp.data[0])

        if signals.reset_ei == HIGH_SIGNAL:
            self.ei = 0
            self.is_isr = True
        elif signals.set_ei == HIGH_SIGNAL:
            self.ei = 1
            self.is_isr = False

        if signals.latch_port:
            self.port = self.dp.data[0]

        self.next_stage()


@dataclass
class ALURes:
    ac: list[int]
    flags: list[int]
    mask: list[int]


class DataPath:
    memory: Memory
    in_dev: InputDevice
    out_devs: dict[int, OutputDevice]

    ir_opcode: Opcode
    ir_addr_mode: AddressMode | None

    data: list[int]

    ar: int
    sp: int
    ac: list[int]
    flags: list[int]
    mask: list[int]

    def __init__(self, memory, in_dev, out_devs):
        self.memory = memory
        self.in_dev = in_dev
        self.out_devs = out_devs

        self.ir_opcode = Opcode.NOP
        self.ir_addr_mode = None

        self.data = ext(0)

        self.ar = 0
        self.sp = len(memory.content)
        self.ac = ext(0)
        self.flags = [0] * 4
        self.mask = ext(0)

    def write_data(self, cu: ControlUnit, signals: SignalContext):
        data_out = mux(signals.sel_out, self.ac, s2v(b2i(self.mask)), s2v(b2i(self.flags)), s2v(cu.pc))

        if signals.wr_out == HIGH_SIGNAL:
            if cu.port in self.out_devs:
                self.out_devs[cu.port].write(data_out[0])

        self.memory.write(mux(signals.sel_addr, self.ar, cu.pc), data_out, signals.wr_mem)

    def read_data(self, cu: ControlUnit, signals: SignalContext):
        data_in = s2v(0)
        if signals.sel_in == SEL_IN_INPUT_DEV:
            if signals.rd_in == HIGH_SIGNAL and cu.port == 0:
                data_in[0] = self.in_dev.read()
        elif signals.sel_in == SEL_IN_MEMORY:
            data_in = self.memory.read(mux(signals.sel_addr, self.ar, cu.pc), signals.rd_mem)

        return mux(signals.sel_ext, data_in, ext(data_in[0]))

    def make_flags(self, res: int, overflow: bool, carry: bool):
        n = 1 if res < 0 else 0
        z = 1 if res == 0 else 0
        v = 1 if overflow else 0
        c = 1 if carry else 0

        return [n, z, v, c]

    def make_mask(self, flags: list[int], signals: SignalContext):
        mask_cond_map = {
            ALU_OP_MASK_EQ: lambda f: f[ZF] == 1,
            ALU_OP_MASK_NE: lambda f: f[ZF] == 0,
            ALU_OP_MASK_LT: lambda f: f[NF] != f[VF],
            ALU_OP_MASK_LE: lambda f: f[NF] != f[VF] or f[ZF] == 1,
            ALU_OP_MASK_GT: lambda f: f[NF] == f[VF] and f[ZF] == 0,
            ALU_OP_MASK_GE: lambda f: f[NF] == f[VF],
        }

        return [1 if mask_cond_map[signals.op](f) else 0 for f in flags]

    def execute_single_alu_op(self, alu_idx: int, signals: SignalContext):  # noqa C901
        a = self.ac[alu_idx]
        b = self.data[alu_idx]

        res = 0
        carry = False
        overflow = False
        if signals.op == ALU_OP_ADD:
            res = a + b

            carry = to_u32(a) + to_u32(b) > 0xFFFF_FFFF
            overflow = (a >= 0 and b >= 0 and res < 0) or (a < 0 and b < 0 and res >= 0)
        elif signals.op == ALU_OP_INC:
            res = a + 1

            carry = to_u32(a) + to_u32(b) > 0xFFFF_FFFF
            overflow = a >= 0 and res < 0
        elif signals.op == ALU_OP_SUB:
            res = a - b

            carry = to_u32(a) < to_u32(b)
            overflow = (a >= 0 and b < 0 and res < 0) or (a < 0 and b >= 0 and res >= 0)
        elif signals.op == ALU_OP_MUL:
            res = a * b
        elif signals.op == ALU_OP_DIV:
            if b == 0:
                raise ValueError("Деление на ноль")

            res = a // b
        elif signals.op == ALU_OP_REM:
            if b == 0:
                raise ValueError("Деление на ноль")

            res = a % b
        elif signals.op == ALU_OP_SHL:
            if b < 0:
                raise ValueError("Отрицательный сдвиг")

            res = a << b
        elif signals.op == ALU_OP_SHR:
            if b < 0:
                raise ValueError("Отрицательный сдвиг")

            res = a >> b
        elif signals.op == ALU_OP_AND:
            res = a & b
        elif signals.op == ALU_OP_OR:
            res = a | b
        elif signals.op == ALU_OP_XOR:
            res = a ^ b
        elif signals.op == ALU_OP_NOT:
            res = ~a
        elif signals.op > ALU_OP_SET_MASK:
            res = a - b

            carry = to_u32(a) < to_u32(b)
            overflow = (a >= 0 and b < 0 and res < 0) or (a < 0 and b >= 0 and res >= 0)

        return res, self.make_flags(res, overflow, carry)

    def execute_alu_op(self, signals: SignalContext):
        alu_res = ALURes(ext(0), [0] * 4, ext(0))

        if signals.op == ALU_OP_SET_FLAGS:
            alu_res.flags = i2b(self.data[0])
            return alu_res
        elif signals.op == ALU_OP_SET_MASK:
            alu_res.mask = i2b(self.data[0])
            return alu_res
        elif signals.op == ALU_OP_MASK_NOT:
            alu_res.mask = [1 - m for m in self.mask]
            return alu_res
        elif signals.op == ALU_OP_NOP:
            return alu_res

        results = []
        flags = []
        for i in range(VECTOR_SIZE):
            res, f = None, None
            try:
                res, f = self.execute_single_alu_op(i, signals)
            except ValueError as e:
                if signals.latch_ac[i] == HIGH_SIGNAL:
                    raise e
                res, f = 0, [0] * 4

            results.append(res)
            flags.append(f)

        alu_res.ac = results
        alu_res.flags = flags[0]
        if signals.op > ALU_OP_SET_MASK:
            alu_res.mask = self.make_mask(flags, signals)

        return alu_res

    def latch_registers(self, cu: ControlUnit, alu_res: ALURes, signals: SignalContext):
        if signals.latch_ir == HIGH_SIGNAL:
            self.ir_opcode, self.ir_addr_mode = extract_instr(self.data)

        next_sp = mux(signals.sel_sp, self.sp + 4, self.sp - 4, self.sp + 16, self.sp - 16, self.sp + self.data[0])
        if signals.latch_ar == HIGH_SIGNAL:
            self.ar = mux(signals.sel_ar, cu.pc, cu.pc + 1, next_sp, self.sp, self.data[0])

        if signals.latch_sp == HIGH_SIGNAL:
            self.sp = next_sp

        for i in range(VECTOR_SIZE):
            if signals.latch_ac[i] == HIGH_SIGNAL:
                self.ac[i] = mux(signals.sel_ac, self.data[i], alu_res.ac[i])

        if signals.latch_flags == HIGH_SIGNAL:
            self.flags = alu_res.flags
        if signals.latch_mask == HIGH_SIGNAL:
            self.mask = alu_res.mask

    def simulate(self, cu: ControlUnit, signals: SignalContext):
        self.write_data(cu, signals)
        self.data = self.read_data(cu, signals)
        alu_res = self.execute_alu_op(signals)
        self.latch_registers(cu, alu_res, signals)


def read_config(config_path: str):
    with open(config_path) as config_file:
        return Config.from_file(config_file)


def fill_memory(config: Config, bin_path: str):
    with open(bin_path, "rb") as f:
        return Memory(config.memory_size, bytearray(f.read()))


def fill_input_queue(config: Config, tg: TickGenerator):
    queue = ast.literal_eval(config.data.strip())

    return InputDevice(tg, queue)


def simulate(bin_path: str, config_path: str):
    config = read_config(config_path)

    tg = TickGenerator()
    memory = fill_memory(config, bin_path)
    in_dev = fill_input_queue(config, tg)
    out_devs = {1: OutputDevice("string"), 2: OutputDevice("array")}

    dp = DataPath(memory, in_dev, out_devs)
    cu = ControlUnit(tg, in_dev, dp)

    log("---------- Execution ----------")

    nb_instr = 0
    for _ in range(config.limit):
        tg.next_tick()
        if cu.stage == Stage.FETCH_INSTR and cu.cycle_step == 0:
            nb_instr += 1

        signals = SignalContext()
        in_dev.send_intrq(signals)

        if config.tick_duration_ms is not None:
            time.sleep(config.tick_duration_ms / 1000)
        cu.simulate(signals)

        if cu.stage == Stage.HALT:
            break

    log("---------- Output ----------")
    log(f"Instructions executed: {nb_instr}")
    log(f"Ticks: {tg.tick}")
    log(f'Output String: "{"".join(out_devs[1].queue)}"')
    log(f"Output Array: {out_devs[2].queue}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("-i", "--input", help="Файл с машинным кодом", required=True)
    parser.add_argument("-c", "--config", help="Файл с конфигурацией", required=True)
    parser.add_argument("-l", "--log", help="Файл логов")

    args = parser.parse_args()

    if args.log is None:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(filename=args.log, filemode="w", level=logging.DEBUG)

    simulate(args.input, args.config)
