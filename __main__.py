import sys
import os
import subprocess

class Type:
    INT = 'Int'
    FLOAT = 'Float'
    BOOL = 'Bool'
    STR = 'Str'
    LIST = lambda inner: f'List[{inner}]'
    VOID = 'Void'
    FUNC = lambda args, ret: f'Func({args})->{ret}'

class Value:
    def __init__(self, value):
        self.value = value

    def get_type(self):
        if isinstance(self.value, int):
            return Type.INT
        elif isinstance(self.value, float):
            return Type.FLOAT
        elif isinstance(self.value, bool):
            return Type.BOOL
        elif isinstance(self.value, str):
            return Type.STR
        elif isinstance(self.value, list):
            return Type.LIST(self.value[0].get_type()) if self.value else Type.LIST(Type.VOID)
        return Type.VOID

    def __str__(self):
        return "[" + ", ".join(str(v) for v in self.value) + "]" if isinstance(self.value, list) else str(self.value)

class Expr:
    def __init__(self, kind, **kwargs):
        self.kind = kind
        self.__dict__.update(kwargs)

class Stmt:
    def __init__(self, kind, **kwargs):
        self.kind = kind
        self.__dict__.update(kwargs)

class Context:
    def __init__(self):
        self.vars = {}
        self.types = {}
        self.funcs = {}
        self.shell = '/bin/sh'

def tokenize(s):
    toks, cur, in_str = [], '', False
    for c in s:
        if c == '"':
            in_str = not in_str
            cur += c
        elif c.isspace() and not in_str:
            if cur:
                toks.append(cur)
                cur = ''
        else:
            cur += c
    if cur:
        toks.append(cur)
    return toks

def parse_type(s):
    return {
        'int': Type.INT, 'float': Type.FLOAT,
        'bool': Type.BOOL, 'str': Type.STR,
        'void': Type.VOID,
    }.get(s, Type.VOID)

def parse_expr(s):
    if s.isdigit(): return Expr('Literal', value=Value(int(s)))
    if s.startswith('"') and s.endswith('"'): return Expr('Literal', value=Value(s[1:-1]))
    if '+' in s:
        lhs, rhs = s.split('+', 1)
        return Expr('BinOp', left=parse_expr(lhs.strip()), op='+', right=parse_expr(rhs.strip()))
    return Expr('Var', name=s)

def parse_stmt(lines):
    line = lines[0]
    if line in ('exit', 'quit'): return Stmt('Exit'), 1
    if line == 'clear': return Stmt('Clear'), 1
    if line == 'help': return Stmt('Help'), 1
    if line.startswith("setshell "): return Stmt('SetShell', shell=line[9:].strip()), 1
    if line.startswith("var "):
        _, rest = line.split("var ", 1)
        name_type, expr = rest.split('=')
        name, ty = name_type.strip().split(':')
        return Stmt('VarDef', name=name.strip(), type=parse_type(ty.strip()), expr=parse_expr(expr.strip())), 1
    if line.startswith("print"):
        inside = line[line.find('(')+1:line.rfind(')')] if '(' in line else line[5:].strip()
        args = [parse_expr(a.strip()) for a in inside.split(',')]
        return Stmt('Print', args=args), 1
    if line.startswith("shell "): return Stmt('Shell', command=tokenize(line[6:])), 1
    return Stmt('ExprStmt', expr=parse_expr(line)), 1

def parse_program(src):
    lines = [line.strip() for line in src.strip().splitlines() if line.strip() and not line.strip().startswith('#')]
    i, stmts = 0, []
    while i < len(lines):
        stmt, used = parse_stmt(lines[i:])
        stmts.append(stmt)
        i += used
    return stmts

def transpile(stmts):
    py_lines = []
    for stmt in stmts:
        if stmt.kind == 'Exit': py_lines.append("exit()")
        elif stmt.kind == 'Clear': py_lines.append("os.system('clear')")
        elif stmt.kind == 'Help': py_lines.append("print('Help: var, print, shell, exit')")
        elif stmt.kind == 'SetShell': py_lines.append(f'shell = "{stmt.shell}"')
        elif stmt.kind == 'VarDef':
            val = stmt.expr.value.value if stmt.expr.kind == "Literal" else stmt.expr.name
            py_lines.append(f'{stmt.name} = {val}')
        elif stmt.kind == 'Print':
            items = []
            for a in stmt.args:
                if a.kind == 'Literal':
                    # Add quotes around string literals to keep Python syntax valid
                    if isinstance(a.value.value, str):
                        items.append(f'"{a.value.value}"')
                    else:
                        items.append(str(a.value.value))
                else:
                    items.append(a.name)
            py_lines.append(f'print({", ".join(items)})')
        elif stmt.kind == 'Shell':
            cmd = " ".join(stmt.command)
            py_lines.append(f'subprocess.run("{cmd}", shell=True)')
        elif stmt.kind == 'ExprStmt':
            if stmt.expr.kind == 'BinOp':
                l = stmt.expr.left.value.value if stmt.expr.left.kind == 'Literal' else stmt.expr.left.name
                r = stmt.expr.right.value.value if stmt.expr.right.kind == 'Literal' else stmt.expr.right.name
                py_lines.append(f'{l} {stmt.expr.op} {r}')
    return "\n".join(py_lines)

def main():
    if len(sys.argv) < 2:
        print("Usage: python transpiler.py <scriptfile>")
        return
    with open(sys.argv[1]) as f:
        src = f.read()
    ctx = Context()
    stmts = parse_program(src)
    code = transpile(stmts)
    exec_globals = {'os': os, 'subprocess': subprocess, 'exit': exit}
    exec(code, exec_globals)

main()