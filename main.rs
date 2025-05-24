use std::cell::RefCell;
use std::collections::HashMap;
use std::env;
use std::fmt;
use std::fs;
use std::io::{self, Write};
use std::process::Command;
use std::rc::Rc;

// ===== Types =====
#[derive(Debug, Clone, PartialEq)]
enum Type {
    Int,
    Float,
    Bool,
    Str,
    List(Box<Type>),
    Void,
    Func(Vec<Type>, Box<Type>),
}

// ===== Value (with Rc/RefCell for GC-like sharing) =====
#[derive(Clone, Debug)]
enum Value {
    Int(i64),
    Float(f64),
    Bool(bool),
    Str(Rc<String>),
    List(Rc<RefCell<Vec<Value>>>),
}

impl Value {
    fn get_type(&self) -> Type {
        match self {
            Value::Int(_)   => Type::Int,
            Value::Float(_) => Type::Float,
            Value::Bool(_)  => Type::Bool,
            Value::Str(_)   => Type::Str,
            Value::List(rc) => {
                // assume empty list of Void if empty, else first element
                let b = rc.borrow();
                if let Some(v) = b.get(0) {
                    Type::List(Box::new(v.get_type()))
                } else {
                    Type::List(Box::new(Type::Void))
                }
            }
        }
    }
}

impl fmt::Display for Value {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Value::Int(i)   => write!(f, "{}", i),
            Value::Float(x) => write!(f, "{}", x),
            Value::Bool(b)  => write!(f, "{}", b),
            Value::Str(s)   => write!(f, "{}", s),
            Value::List(rc) => {
                let items = rc.borrow()
                              .iter()
                              .map(|v| v.to_string())
                              .collect::<Vec<_>>()
                              .join(", ");
                write!(f, "[{}]", items)
            }
        }
    }
}

// ===== AST =====
#[derive(Clone, Debug)]
enum Expr {
    Literal(Value),
    Var(String),
    BinOp(Box<Expr>, char, Box<Expr>),
    Call(String, Vec<Expr>),
}

#[derive(Clone, Debug)]
enum Stmt {
    VarDef(String, Type, Expr),
    Print(Vec<Expr>),
    Shell(Vec<String>),
    SetShell(String),
    If(Expr, Vec<Stmt>, Vec<Stmt>),
    While(Expr, Vec<Stmt>),
    FuncDef(String, Vec<(String,Type)>, Type, Vec<Stmt>),
    Exit,
    Clear,
    Help,
    ExprStmt(Expr),
}

// ===== Context & TypeContext =====
struct Context {
    vars:  HashMap<String, Value>,
    types: HashMap<String, Type>,
    funcs: HashMap<String,(Vec<(String,Type)>,Type,Vec<Stmt>)>,
    shell: String,
}

impl Context {
    fn new() -> Self {
        Self {
            vars:  HashMap::new(),
            types: HashMap::new(),
            funcs: HashMap::new(),
            shell: "/bin/sh".into(),
        }
    }
}

// ===== Tokenizer & Parser =====
fn tokenize(s: &str) -> Vec<String> {
    let mut toks = Vec::new();
    let mut cur = String::new();
    let mut in_str = false;
    for c in s.chars() {
        match c {
            '"' => { in_str = !in_str; cur.push(c) }
            ' ' if !in_str => {
                if !cur.is_empty() { toks.push(cur.clone()); cur.clear() }
            }
            _ => cur.push(c),
        }
    }
    if !cur.is_empty() { toks.push(cur) }
    toks
}

// Simplified parser: single-line statements or blocks
fn parse_program(src: &str) -> Vec<Stmt> {
    let mut stmts = Vec::new();
    let lines: Vec<&str> = src.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .collect();
    let mut i = 0;
    while i < lines.len() {
        let (stmt, cons) = parse_stmt(&lines[i..]);
        stmts.push(stmt);
        i += cons;
    }
    stmts
}

fn parse_stmt(lines: &[&str]) -> (Stmt, usize) {
    let line = lines[0];
    // builtins
    if line=="exit" || line=="quit" { return (Stmt::Exit,1) }
    if line=="clear"              { return (Stmt::Clear,1) }
    if line=="help"               { return (Stmt::Help,1) }
    // setshell
    if let Some(arg) = line.strip_prefix("setshell ") {
        return (Stmt::SetShell(arg.into()),1);
    }
    // var x:Type = expr
    if let Some(rest) = line.strip_prefix("var ") {
        if let Some((left, expr)) = rest.split_once('=') {
            let left = left.trim();
            if let Some((name, tystr)) = left.split_once(':') {
                let ty = parse_type(tystr.trim());
                let e = parse_expr(expr.trim());
                return (Stmt::VarDef(name.trim().into(), ty, e),1);
            }
        }
    }
    // print(...)
    if let Some(r) = line.strip_prefix("print") {
        let inside = if let Some(i) = r.find('(') {
            &r[i+1..r.len()-1]
        } else {
            r.trim()
        };
        let args = inside.split(',').map(|a| parse_expr(a.trim())).collect();
        return (Stmt::Print(args),1);
    }
    // shell cmd...
    if let Some(r) = line.strip_prefix("shell ") {
        let parts = tokenize(r);
        return (Stmt::Shell(parts),1);
    }
    // if…then…[else…]end
    if line.starts_with("if ") {
        let cond = parse_expr(&line[3..line.find("then").unwrap()].trim());
        let mut then_b=Vec::new(); let mut else_b=Vec::new();
        let mut idx=1;
        while idx<lines.len() {
            let t=lines[idx].trim();
            if t=="else"||t=="end"{ break }
            then_b.push(parse_stmt(&lines[idx..]).0); idx+=1;
        }
        if idx<lines.len() && lines[idx].trim()=="else" {
            idx+=1;
            while idx<lines.len() {
                let t=lines[idx].trim();
                if t=="end"{ break }
                else_b.push(parse_stmt(&lines[idx..]).0); idx+=1;
            }
        }
        return (Stmt::If(cond,then_b,else_b), idx+1);
    }
    // while…do…end
    if line.starts_with("while ") {
        let cond = parse_expr(&line[6..line.find("do").unwrap()].trim());
        let mut body = Vec::new();
        let mut idx=1;
        while idx<lines.len() && lines[idx].trim()!="end" {
            body.push(parse_stmt(&lines[idx..]).0); idx+=1;
        }
        return (Stmt::While(cond,body), idx+1);
    }
    // fn name(p:Type,…)->Type … end
    if line.starts_with("fn ") {
        let after=&line[3..];
        let name=&after[..after.find('(').unwrap()];
        let rest=&after[after.find('(').unwrap()+1..after.find(')').unwrap()];
        let parts: Vec<&str> = rest.split(',').map(str::trim).filter(|s|!s.is_empty()).collect();
        let mut params=Vec::new();
        for p in parts {
            let (n,t)=p.split_once(':').unwrap();
            params.push((n.into(), parse_type(t)));
        }
        let ret_ty= if let Some(rp) = after.find(")->") {
            parse_type(after[rp+3..].trim())
        } else { Type::Void };
        let mut body=Vec::new(); let mut idx=1;
        while idx<lines.len() && lines[idx].trim()!="end" {
            body.push(parse_stmt(&lines[idx..]).0); idx+=1;
        }
        return (Stmt::FuncDef(name.into(),params,ret_ty,body), idx+1);
    }
    // fallback expr
    (Stmt::ExprStmt(parse_expr(line)),1)
}

fn parse_type(s: &str) -> Type {
    match s.to_lowercase().as_str() {
        "int"    => Type::Int,
        "float"  => Type::Float,
        "bool"   => Type::Bool,
        "str" | "string" => Type::Str,
        t if t.starts_with("list<") && t.ends_with('>') => {
            let inner = &t[5..t.len()-1];
            Type::List(Box::new(parse_type(inner)))
        }
        _ => Type::Void,
    }
}

fn parse_expr(s: &str) -> Expr {
    let s=s.trim();
    if s.starts_with('"') && s.ends_with('"') {
        return Expr::Literal(Value::Str(Rc::new(s[1..s.len()-1].into())));
    }
    if s=="true"  { return Expr::Literal(Value::Bool(true)) }
    if s=="false" { return Expr::Literal(Value::Bool(false)) }
    if s.contains('.') {
        if let Ok(f)=s.parse() { return Expr::Literal(Value::Float(f)) }
    }
    if let Ok(i)=s.parse() { return Expr::Literal(Value::Int(i)) }
    if let Some(idx)=s.find('+') {
        return Expr::BinOp(
            Box::new(parse_expr(&s[..idx])),
            '+',
            Box::new(parse_expr(&s[idx+1..]))
        );
    }
    if let Some(idx)=s.find('(') {
        let name=&s[..idx];
        let args=&s[idx+1..s.len()-1];
        let vs= if args.is_empty() {
            Vec::new()
        } else {
            args.split(',').map(|a| parse_expr(a.trim())).collect()
        };
        return Expr::Call(name.into(),vs);
    }
    Expr::Var(s.into())
}

// ===== Type Checker =====
fn type_check_stmt(stmt: &Stmt, ctx: &mut Context) -> Result<(),String> {
    match stmt {
        Stmt::VarDef(name, ty, expr) => {
            let et=type_check_expr(expr,ctx)?;
            if &et!=ty {
                return Err(format!("Type mismatch for {}: expected {:?}, got {:?}", name, ty, et));
            }
            ctx.types.insert(name.clone(), ty.clone());
            Ok(())
        }
        Stmt::Print(exprs) => {
            for expr in exprs {
                type_check_expr(expr, ctx)?;
            }
            Ok(())
        }
        Stmt::Shell(_) | Stmt::SetShell(_) | Stmt::Exit|Stmt::Clear|Stmt::Help => Ok(()),
        Stmt::If(cond,t,e) => {
            if type_check_expr(cond,ctx)?!=Type::Bool {
                return Err("Condition must be bool".into())
            }
            for s in t { type_check_stmt(s,ctx)?; }
            for s in e { type_check_stmt(s,ctx)?; }
            Ok(())
        }
        Stmt::While(cond,body) => {
            if type_check_expr(cond,ctx)?!=Type::Bool {
                return Err("While cond must be bool".into())
            }
            for s in body { type_check_stmt(s,ctx)?; }
            Ok(())
        }
        Stmt::FuncDef(name,params,ret,body) => {
            let prev=ctx.types.clone();
            for (n,ty) in params { ctx.types.insert(n.clone(),ty.clone()); }
            for s in body { type_check_stmt(s,ctx)?; }
            ctx.types=prev;
            ctx.funcs.insert(name.clone(),(params.clone(),ret.clone(),body.clone()));
            Ok(())
        }
        Stmt::ExprStmt(e) => { let _=type_check_expr(e,ctx)?; Ok(()) }
    }
}

fn type_check_expr(expr: &Expr, ctx: &Context) -> Result<Type,String> {
    match expr {
        Expr::Literal(v) => Ok(v.get_type()),
        Expr::Var(n) => ctx.types.get(n)
            .cloned()
            .ok_or(format!("Unknown var {}",n)),
        Expr::BinOp(a,op,b) => {
            let at=type_check_expr(a,ctx)?;
            let bt=type_check_expr(b,ctx)?;
            if at!=bt { return Err("Type mismatch in binop".into()) }
            Ok(at)
        }
        Expr::Call(_,_) => Err("Function calls not yet typed".into()),
    }
}

// ===== Evaluator =====
fn eval_expr(e: &Expr, ctx: &mut Context) -> Option<Value> {
    match e {
        Expr::Literal(v) => Some(v.clone()),
        Expr::Var(n)     => ctx.vars.get(n).cloned(),
        Expr::BinOp(a,op,b) => {
            let x=eval_expr(a,ctx)?; let y=eval_expr(b,ctx)?;
            Some(match (x,y,*op) {
                (Value::Int(p),Value::Int(q),'+')   => Value::Int(p+q),
                (Value::Int(p),Value::Int(q),'-')   => Value::Int(p-q),
                (Value::Int(p),Value::Int(q),'*')   => Value::Int(p*q),
                (Value::Int(p),Value::Int(q),'/')   => Value::Int(p/q),
                (Value::Float(p),Value::Float(q),'+') => Value::Float(p+q),
                (Value::Float(p),Value::Float(q),'-') => Value::Float(p-q),
                (Value::Float(p),Value::Float(q),'*') => Value::Float(p*q),
                (Value::Float(p),Value::Float(q),'/') => Value::Float(p/q),
                _ => return None,
            })
        }
        Expr::Call(_,_) => None, // future: call user funcs
    }
}

fn exec_stmt(stmt: &Stmt, ctx: &mut Context) -> bool {
    if let Err(e) = type_check_stmt(stmt,ctx) {
        eprintln!("Type error: {}",e);
        return false;
    }
    match stmt {
        Stmt::VarDef(n,_,e) => {
            if let Some(v)=eval_expr(e,ctx) { ctx.vars.insert(n.clone(),v); }
        }
        Stmt::Print(es) => {
            let out=es.iter()
                .map(|e| eval_expr(e,ctx).unwrap().to_string())
                .collect::<Vec<_>>()
                .join(" ");
            println!("{}", out);
        }
        Stmt::Shell(cmds) => {
            if cmds.is_empty() { return false; }
            let mut c = Command::new(&cmds[0]);
            for arg in &cmds[1..] { c.arg(arg); }
            let _=c.status();
        }
        Stmt::SetShell(sh) => ctx.shell=sh.clone(),
        Stmt::If(cond,t,e) => {
            if matches!(eval_expr(cond,ctx),Some(Value::Bool(true))) {
                for s in t { if exec_stmt(s,ctx) { return true; } }
            } else {
                for s in e { if exec_stmt(s,ctx) { return true; } }
            }
        }
        Stmt::While(cond,body) => {
            while matches!(eval_expr(cond,ctx),Some(Value::Bool(true))) {
                for s in body { if exec_stmt(s,ctx) { return true; } }
            }
        }
        Stmt::FuncDef(_,_,_,_) => { /* stored in type_check */ }
        Stmt::Exit => return true,
        Stmt::Clear => { print!("\x1B[2J\x1B[1;1H"); io::stdout().flush().unwrap(); }
        Stmt::Help => {
            println!(r"help:
 var x:Type = expr
 print(expr,…)
 shell cmd…
 setshell /bin/bash
 if…then…else…end
 while…do…end
 fn name(p:Type,…)->Type…end
 exit, clear, help");
        }
        Stmt::ExprStmt(e) => { let _=eval_expr(e,ctx); }
    }
    false
}

fn run_script(path: &str, ctx: &mut Context) {
    match fs::read_to_string(path) {
        Ok(src) => for stmt in &parse_program(&src) {
            if exec_stmt(stmt,ctx) { break; }
        },
        Err(_) => eprintln!("Cannot open file: {}",path),
    }
}

fn repl(ctx: &mut Context) {
    let stdin = io::stdin();
    loop {
        print!("dolphin> "); io::stdout().flush().unwrap();
        let mut line=String::new();
        if stdin.read_line(&mut line).is_err() { break; }
        for part in line.trim().split(';') {
            if part.trim().is_empty() { continue; }
            let (stmt,_) = parse_stmt(&[part.trim()]);
            if exec_stmt(&stmt,ctx) { return; }
        }
    }
}

fn main() {
    let mut ctx = Context::new();
    let args: Vec<String> = env::args().collect();
    if args.len()==2 {
        run_script(&args[1],&mut ctx);
    } else {
        repl(&mut ctx);
    }
}