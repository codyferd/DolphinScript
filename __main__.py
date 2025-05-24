import sys
import os
import subprocess
from typing import Any, Dict, List, Tuple

# ===== Types =====
class Type:
    INT = 'int'
    FLOAT = 'float'
    BOOL = 'bool'
    STR = 'str'
    LIST = 'list'
    VOID = 'void'

# ===== Value =====
class Value:
    def __init__(self, value: Any):
        self.value = value

    def get_type(self) -> str:
        if isinstance(self.value, bool): return Type.BOOL
        if isinstance(self.value, int): return Type.INT
        if isinstance(self.value, float): return Type.FLOAT
        if isinstance(self.value, str): return Type.STR
        if isinstance(self.value, list): return Type.LIST
        return Type.VOID

# ===== AST Nodes =====
class Expr: pass
class Stmt: pass

class Literal(Expr):
    def __init__(self, value: Value): self.value = value

class VarRef(Expr):
    def __init__(self, name: str): self.name = name

class TypedExpr(Expr):
    def __init__(self, typ: str, expr: Expr):
        self.typ = typ
        self.expr = expr
class BinOp(Expr):
    def __init__(self, left: Expr, op: str, right: Expr):
        self.left, self.op, self.right = left, op, right

class Call(Expr):
    def __init__(self, name: str, args: List[Expr]): self.name, self.args = name, args

class VarDef(Stmt):
    def __init__(self, name: str, typ: str, expr: Expr):
        self.name, self.typ, self.expr = name, typ, expr

class Print(Stmt):
    def __init__(self, exprs: List[Expr]): self.exprs = exprs

class Shell(Stmt):
    def __init__(self, command: str): self.command = command

class SetShell(Stmt):
    def __init__(self, shell: str): self.shell = shell

class PythonExec(Stmt):
    def __init__(self, code: str): self.code = code

class Exit(Stmt): pass
class Clear(Stmt): pass
class Help(Stmt): pass
class ExprStmt(Stmt):
    def __init__(self, expr: Expr): self.expr = expr

# ===== Context =====
class Context:
    def __init__(self):
        self.vars: Dict[str, Value] = {}
        self.types: Dict[str, str] = {}
        self.shell: str = '/bin/sh'

# ===== Tokenizer =====
def tokenize(line: str) -> List[str]:
    toks, cur, in_str = [], '', False
    for c in line:
        if c == '"': in_str = not in_str; cur += c
        elif c.isspace() and not in_str:
            if cur: toks.append(cur); cur = ''
        else: cur += c
    if cur: toks.append(cur)
    return toks

# ===== Parser =====
def parse_expr(s: str) -> Expr:
    s = s.strip()
    # Detect type annotation: e.g. str:"Hello"
    if ':' in s and not s.startswith('"') and not s[0].isdigit():
        typ, rest = s.split(':', 1)
        if typ in (Type.INT, Type.FLOAT, Type.BOOL, Type.STR, Type.LIST):
            return TypedExpr(typ, parse_expr(rest.strip()))
    # existing parsing follows
    if s.startswith('"') and s.endswith('"'):
        return Literal(Value(s[1:-1]))
    if s.isdigit(): return Literal(Value(int(s)))
    if s.replace('.', '', 1).isdigit() and s.count('.') == 1:
        return Literal(Value(float(s)))
    for op in ['+', '-', '*', '/', '==', '!=', '<', '>']:
        if op in s:
            left, right = s.split(op, 1)
            return BinOp(parse_expr(left), op, parse_expr(right))
    if '(' in s and s.endswith(')'):
        name, args = s.split('(', 1)
        args = args[:-1]
        return Call(name.strip(), [parse_expr(a) for a in args.split(',') if a.strip()])
    return VarRef(s)

def parse_stmt(line: str) -> Stmt:
    if line in ('exit', 'quit'): 
        return Exit()
    if line == 'clear': 
        return Clear()
    if line == 'help': 
        return Help()
    if line.startswith('chsh '): 
        return SetShell(line[5:].strip())
    if line.startswith('sh '): 
        return Shell(line[3:].strip())
    if line.startswith('py '): 
        return PythonExec(line[3:].strip())
    if line.startswith('var '):
        _, rest = line.split('var ', 1)
        name_type, expr = rest.split('=', 1)
        name, typ = name_type.split(':', 1)
        return VarDef(name.strip(), typ.strip(), parse_expr(expr.strip()))
    if line.startswith('print(') and line.endswith(')'):
        args = line[6:-1]
        return Print([parse_expr(a.strip()) for a in args.split(',') if a.strip()])
    return ExprStmt(parse_expr(line))

def parse_program(src: str) -> List[Stmt]:
    lines = [l.strip() for l in src.splitlines() if l.strip() and not l.strip().startswith('#')]
    stmts = []
    for line in lines:
        parts = [part.strip() for part in line.split(';') if part.strip()]
        for part in parts:
            stmts.append(parse_stmt(part))
    return stmts

# ===== Type Checker =====
def type_check_expr(e: Expr, ctx: Context) -> str:
    if isinstance(e, TypedExpr):
        t = e.typ
        expr_t = type_check_expr(e.expr, ctx)
        if t != expr_t:
            raise TypeError(f"Type annotation {t} does not match expression type {expr_t}")
        return t
    if isinstance(e, Literal): return e.value.get_type()
    if isinstance(e, VarRef): return ctx.types.get(e.name, Type.VOID)
    if isinstance(e, BinOp):
        lt = type_check_expr(e.left, ctx)
        rt = type_check_expr(e.right, ctx)
        if lt != rt: raise TypeError(f"Type mismatch {lt} {e.op} {rt}")
        return lt
    if isinstance(e, Call):
        # optionally check return type of call
        return Type.VOID
    return Type.VOID

def type_check_stmt(s: Stmt, ctx: Context):
    if isinstance(s, VarDef):
        t = type_check_expr(s.expr, ctx)
        if t != s.typ: raise TypeError(f"Expected {s.typ}, got {t}")
        ctx.types[s.name] = s.typ

# ===== Executor =====
def execute_stmt(s: Stmt, ctx: Context):
    if isinstance(s, Exit): sys.exit(0)
    if isinstance(s, Clear): os.system('cls' if os.name == 'nt' else 'clear')
    if isinstance(s, Help):
        print("DolphinScript Help:\n  var x:type = expr\n  print(expr)\n  sh command\n  py code\n  exit\n  help\n  clear")
    elif isinstance(s, SetShell): ctx.shell = s.shell
    elif isinstance(s, Shell): subprocess.call(s.command, shell=True, executable=ctx.shell)
    elif isinstance(s, PythonExec): exec(s.code, {}, ctx.vars)
    elif isinstance(s, VarDef):
        val = eval_expr(s.expr, ctx)
        ctx.vars[s.name] = val
    elif isinstance(s, Print):
        print(' '.join(str(eval_expr(e, ctx).value) for e in s.exprs))
    elif isinstance(s, ExprStmt): eval_expr(s.expr, ctx)

def eval_expr(e: Expr, ctx: Context) -> Value:
    if isinstance(e, Literal): return e.value
    if isinstance(e, VarRef): return ctx.vars.get(e.name, Value(None))
    if isinstance(e, BinOp):
        l = eval_expr(e.left, ctx).value
        r = eval_expr(e.right, ctx).value
        return Value(eval(f"{repr(l)} {e.op} {repr(r)}"))
    if isinstance(e, Call):
        fn = ctx.vars.get(e.name)
        if callable(fn):
            args = [eval_expr(a, ctx).value for a in e.args]
            return Value(fn(*args))
    return Value(None)

# ===== REPL =====
def run_repl():
    print("DolphinScript REPL. Type 'help' for help.")
    ctx = Context()
    while True:
        try:
            line = input('>>> ').strip()
            for part in [p.strip() for p in line.split(';') if p.strip()]:
                stmt = parse_stmt(part)
                type_check_stmt(stmt, ctx)
                execute_stmt(stmt, ctx)
        except Exception as e:
            print("Error:", e)

# ===== Main =====
def main():
    if len(sys.argv) == 1:
        run_repl()
    elif len(sys.argv) == 2 and sys.argv[1].endswith(".dsc"):
        with open(sys.argv[1]) as f:
            src = f.read()
        prog = parse_program(src)
        ctx = Context()
        for stmt in prog:
            type_check_stmt(stmt, ctx)
            execute_stmt(stmt, ctx)
    else:
        print("Usage: dscript.dsc OR run without arguments for REPL")

if __name__ == "__main__":
    main()