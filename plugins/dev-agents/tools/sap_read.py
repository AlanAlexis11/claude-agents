"""
sap_read.py — Acceso de SOLO LECTURA a SAP GUI vía SAP GUI Scripting (Windows).

Pensado para que lo use el agente "investigador" de Claude Code.
Requisitos:
  - Windows con SAP GUI abierto y sesión iniciada.
  - SAP GUI Scripting habilitado (cliente: Opciones > Accesibilidad y scripting;
    servidor: parámetro sapgui/user_scripting = TRUE).
  - pip install pywin32

Barreras de solo lectura:
  1. Solo transacciones de la lista permitida (visualización / reportes).
  2. Solo teclas seguras: Enter, F3 (atrás), F8 (ejecutar), F12 (cancelar popup).
     Nunca se envía Ctrl+S / F11 (grabar), ni se presionan botones ni doble clic.
  3. Después de cada acción se verifica que la transacción actual siga permitida;
     si no, se sale con /n y se devuelve error.
  La garantía real es que el usuario SAP del agente tenga un rol de solo visualización.

Uso:
  python sap_read.py sessions
  python sap_read.py open IW29
  python sap_read.py inspect
  python sap_read.py set "wnd[0]/usr/ctxtDATUV=01.11.2026" ...
  python sap_read.py execute | enter | back | close-popup
  python sap_read.py read [--max-rows N] [--out archivo.csv]

Opciones globales: --conn N --sess N (o variables SAP_READ_CONN / SAP_READ_SESS).
Transacciones extra permitidas: SAP_READ_EXTRA_TCODES="ZREP1,ZREP2" (solo de consulta).
Se instala en ~/.claude/tools/sap_read.py (lo usa el agente "investigador" del plugin dev-agents).
"""

import argparse
import csv
import json
import os
import re
import sys

ALLOWED_TCODES = {
    # Avisos (visualización)
    "IW29", "IW23", "IW59",
    # Medidas / prestaciones (visualización)
    "IW65", "IW67",
    # Órdenes (visualización)
    "IW39", "IW33", "IW49",
    # Equipos / ubicaciones técnicas (visualización)
    "IE03", "IE05", "IL03", "IL05",
    # Pedidos / contratos marco (visualización)
    "ME23N", "ME33K", "ME2K", "ME2N", "ME3N",
    # Reportes Z de consulta del proyecto
    # ZNROLIQUIDACIONHES ("Generación de número de liquidación") queda AFUERA a propósito: hasta que el equipo
    # funcional de SAP confirme que es de solo lectura, solo se habilita a mano con SAP_READ_EXTRA_TCODES.
    "ZR010_TAVISOS",
    # Menú / pantalla inicial
    "SESSION_MANAGER", "SMEN", "",
}
ALLOWED_TCODES |= {
    t.strip().upper()
    for t in os.environ.get("SAP_READ_EXTRA_TCODES", "").split(",")
    if t.strip()
}

VKEY_ENTER, VKEY_BACK, VKEY_EXECUTE, VKEY_CANCEL = 0, 3, 8, 12
INPUT_TYPES = {"GuiTextField", "GuiCTextField", "GuiComboBox", "GuiCheckBox", "GuiRadioButton"}


def fail(msg, code=1):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


def out(data):
    print(json.dumps({"ok": True, **data}, ensure_ascii=False, indent=2, default=str))


# ---------- Conexión ----------

def scripting_engine():
    try:
        import win32com.client  # noqa
    except ImportError:
        fail("Falta pywin32: pip install pywin32 (y correr en Windows).")
    try:
        sapgui = win32com.client.GetObject("SAPGUI")
        return sapgui.GetScriptingEngine
    except Exception as e:
        fail(f"No se encontró SAP GUI abierto o scripting deshabilitado: {e}")


def get_session(args):
    app = scripting_engine()
    try:
        conn = app.Children(int(args.conn))
        return conn.Children(int(args.sess))
    except Exception:
        fail(f"No existe la conexión {args.conn} / sesión {args.sess}. Usá 'sessions'.")


def current_tcode(session):
    return (session.Info.Transaction or "").upper()


def guard(session):
    """Verifica que la transacción actual sea de solo lectura; si no, sale."""
    tcode = current_tcode(session)
    if tcode not in ALLOWED_TCODES:
        try:
            session.findById("wnd[0]/tbar[0]/okcd").text = "/n"
            session.findById("wnd[0]").sendVKey(VKEY_ENTER)
        except Exception:
            pass
        fail(f"La transacción '{tcode}' no está permitida (solo lectura). Se salió con /n.")
    return tcode


def status_bar(session):
    try:
        sb = session.findById("wnd[0]/sbar")
        return {"text": sb.Text, "type": sb.MessageType}
    except Exception:
        return None


def popup_info(session):
    try:
        w = session.findById("wnd[1]")
        texts = [e["text"] for e in walk_elements(w) if e.get("text")]
        return {"title": w.Text, "texts": texts[:30]}
    except Exception:
        return None


def send_vkey(session, key):
    if key not in (VKEY_ENTER, VKEY_BACK, VKEY_EXECUTE):
        fail("Tecla no permitida.")
    if popup_info(session):
        fail("Hay un popup abierto. Revisalo con 'inspect' y cerralo con 'close-popup'.")
    session.findById("wnd[0]").sendVKey(key)


# ---------- Lectura de pantalla ----------

def children(obj):
    try:
        ch = obj.Children
        return [ch.ElementAt(i) for i in range(ch.Count)]
    except Exception:
        return []


def walk(obj):
    yield obj
    for c in children(obj):
        yield from walk(c)


def walk_elements(root):
    items = []
    for el in walk(root):
        try:
            t = el.Type
        except Exception:
            continue
        info = {"id": el.Id.split("/app/con[")[-1].split("/", 2)[-1], "type": t}
        try:
            if el.Text:
                info["text"] = el.Text
        except Exception:
            pass
        if t in INPUT_TYPES:
            try:
                info["changeable"] = bool(el.Changeable)
            except Exception:
                pass
            if t in ("GuiCheckBox", "GuiRadioButton"):
                info["selected"] = bool(el.Selected)
            try:
                info["tooltip"] = el.Tooltip
            except Exception:
                pass
        if t == "GuiShell":
            try:
                info["subtype"] = el.SubType
            except Exception:
                pass
        items.append(info)
    return items


def find_grids(session):
    grids, tables = [], []
    for el in walk(session.findById("wnd[0]/usr")):
        try:
            if el.Type == "GuiShell" and el.SubType == "GridView":
                grids.append(el)
            elif el.Type == "GuiTableControl":
                tables.append(el)
        except Exception:
            continue
    return grids, tables


def read_alv(grid, max_rows):
    cols_coll = grid.ColumnOrder
    cols = [cols_coll.ElementAt(i) for i in range(cols_coll.Count)] if hasattr(cols_coll, "Count") else list(cols_coll)
    titles = []
    for c in cols:
        try:
            titles.append(grid.GetDisplayedColumnTitle(c) or c)
        except Exception:
            titles.append(c)
    total = grid.RowCount
    n = min(total, max_rows)
    try:
        visible = max(int(grid.VisibleRowCount), 1)
    except Exception:
        visible = 30
    rows = []
    for r in range(n):
        if r % visible == 0:
            grid.FirstVisibleRow = r
        rows.append([grid.GetCellValue(r, c) for c in cols])
    return titles, rows, total


def read_table_control(table, max_rows):
    cols = table.Columns
    titles = [cols.ElementAt(i).Title for i in range(cols.Count)]
    total = table.RowCount
    rows, pos = [], 0
    while pos < min(total, max_rows):
        table.VerticalScrollbar.Position = pos
        table = table.Parent.findById(table.Id.split("/")[-1])  # refrescar referencia
        vis = table.VisibleRowCount
        for r in range(vis):
            if pos + r >= min(total, max_rows):
                break
            try:
                rows.append([table.GetCell(r, c).Text for c in range(cols.Count)])
            except Exception:
                break
        pos += vis
    return titles, rows, total


def read_abap_list(session, max_rows):
    """Listas clásicas: etiquetas lbl[col,fila] dentro de usr."""
    cells = {}
    for el in walk(session.findById("wnd[0]/usr")):
        try:
            m = re.search(r"lbl\[(\d+),(\d+)\]$", el.Id)
            if m:
                col, row = int(m.group(1)), int(m.group(2))
                cells.setdefault(row, []).append((col, el.Text))
        except Exception:
            continue
    lines = []
    for row in sorted(cells)[:max_rows]:
        lines.append(" ".join(t for _, t in sorted(cells[row])))
    return lines


# ---------- Comandos ----------

def cmd_sessions(args):
    app = scripting_engine()
    res = []
    for ci, conn in enumerate(children(app)):
        for si, s in enumerate(children(conn)):
            info = s.Info
            res.append({
                "conn": ci, "sess": si, "system": info.SystemName,
                "client": info.Client, "user": info.User,
                "transaction": info.Transaction,
            })
    out({"sessions": res})


def cmd_open(args):
    tcode = args.tcode.strip().upper()
    if tcode not in ALLOWED_TCODES:
        fail(f"'{tcode}' no está en la lista de transacciones de solo lectura.")
    s = get_session(args)
    if popup_info(s):
        fail("Hay un popup abierto. Cerralo con 'close-popup'.")
    s.findById("wnd[0]/tbar[0]/okcd").text = f"/n{tcode}"
    s.findById("wnd[0]").sendVKey(VKEY_ENTER)
    guard(s)
    out({"transaction": current_tcode(s), "title": s.findById("wnd[0]").Text, "status": status_bar(s)})


def cmd_inspect(args):
    s = get_session(args)
    tcode = guard(s)
    grids, tables = find_grids(s)
    elements = walk_elements(s.findById("wnd[0]/usr"))
    relevant = [e for e in elements if e["type"] in INPUT_TYPES or e["type"] in ("GuiLabel", "GuiShell", "GuiTableControl") or e.get("text")]
    out({
        "transaction": tcode,
        "title": s.findById("wnd[0]").Text,
        "status": status_bar(s),
        "popup": popup_info(s),
        "alv_grids": len(grids),
        "table_controls": len(tables),
        "elements": relevant[: args.limit],
        "truncated": len(relevant) > args.limit,
    })


def cmd_set(args):
    s = get_session(args)
    guard(s)
    if popup_info(s):
        fail("Hay un popup abierto. Cerralo con 'close-popup'.")
    done = []
    for pair in args.assignments:
        if "=" not in pair:
            fail(f"Formato inválido '{pair}'. Usá ID=VALOR.")
        fid, value = pair.split("=", 1)
        if not fid.startswith("wnd[0]/usr/"):
            fail(f"Solo se pueden completar campos dentro de wnd[0]/usr/: {fid}")
        try:
            el = s.findById(fid)
        except Exception:
            fail(f"No existe el campo {fid}. Usá 'inspect'.")
        if el.Type not in INPUT_TYPES:
            fail(f"{fid} es {el.Type}; solo se permiten campos de entrada.")
        if el.Type in ("GuiCheckBox",):
            el.Selected = value.lower() in ("1", "true", "x", "si", "sí")
        elif el.Type == "GuiRadioButton":
            el.Select()
        elif el.Type == "GuiComboBox":
            el.Key = value
        else:
            el.Text = value
        done.append(fid)
    out({"set": done})


def cmd_vkey(key):
    def run(args):
        s = get_session(args)
        guard(s)
        send_vkey(s, key)
        tcode = guard(s)
        out({"transaction": tcode, "title": s.findById("wnd[0]").Text,
             "status": status_bar(s), "popup": popup_info(s)})
    return run


def cmd_close_popup(args):
    s = get_session(args)
    if not popup_info(s):
        out({"message": "No hay popup."})
        return
    s.findById("wnd[1]").sendVKey(VKEY_CANCEL)  # Cancelar, nunca confirmar
    guard(s)
    out({"message": "Popup cancelado.", "status": status_bar(s)})


def cmd_read(args):
    s = get_session(args)
    tcode = guard(s)
    grids, tables = find_grids(s)
    if grids:
        titles, rows, total = read_alv(grids[args.index] if args.index < len(grids) else grids[0], args.max_rows)
        kind = "alv"
    elif tables:
        titles, rows, total = read_table_control(tables[0], args.max_rows)
        kind = "table_control"
    else:
        lines = read_abap_list(s, args.max_rows)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            out({"transaction": tcode, "kind": "abap_list", "lines": len(lines), "file": args.out})
        else:
            out({"transaction": tcode, "kind": "abap_list", "lines": lines})
        return

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(titles)
            w.writerows(rows)
        out({"transaction": tcode, "kind": kind, "columns": titles,
             "rows_read": len(rows), "rows_total": total, "file": args.out,
             "preview": rows[:5]})
    else:
        out({"transaction": tcode, "kind": kind, "columns": titles,
             "rows_read": len(rows), "rows_total": total,
             "rows": rows[: min(len(rows), 200)],
             "note": "Para más de 200 filas usá --out archivo.csv"})


def main():
    p = argparse.ArgumentParser(description="SAP GUI de solo lectura")
    p.add_argument("--conn", default=os.environ.get("SAP_READ_CONN", "0"))
    p.add_argument("--sess", default=os.environ.get("SAP_READ_SESS", "0"))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sessions").set_defaults(func=cmd_sessions)

    o = sub.add_parser("open"); o.add_argument("tcode"); o.set_defaults(func=cmd_open)

    i = sub.add_parser("inspect"); i.add_argument("--limit", type=int, default=300)
    i.set_defaults(func=cmd_inspect)

    st = sub.add_parser("set"); st.add_argument("assignments", nargs="+")
    st.set_defaults(func=cmd_set)

    sub.add_parser("enter").set_defaults(func=cmd_vkey(VKEY_ENTER))
    sub.add_parser("execute").set_defaults(func=cmd_vkey(VKEY_EXECUTE))
    sub.add_parser("back").set_defaults(func=cmd_vkey(VKEY_BACK))
    sub.add_parser("close-popup").set_defaults(func=cmd_close_popup)

    r = sub.add_parser("read")
    r.add_argument("--max-rows", type=int, default=2000)
    r.add_argument("--out")
    r.add_argument("--index", type=int, default=0, help="Índice de grilla ALV si hay varias")
    r.set_defaults(func=cmd_read)

    args = p.parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
