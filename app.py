import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
import requests
import re
import time
import plotly.express as px

# --- CONFIGURACIÓN DE PÁGINA (TU CÓDIGO ORIGINAL) ---
st.set_page_config(page_title="Control Batch Visual", layout="wide")

# --- CONSTANTES DE FORMATOS (TU CÓDIGO ORIGINAL) ---
FORMATO_COMUN = "Común (1/3)"
FORMATO_ROMANO = "Romanos (I/III)"
FORMATO_PAREJAS = "Parejas (1-2)"

# --- CONFIGURACIÓN GOOGLE SHEETS Y APP SCRIPT (TU CÓDIGO ORIGINAL) ---
JSON_FILE = 'credentials.json'
SCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
ID_REGISTROS = "1eNjyzmkBvnnaE4v1hHM1AR-hhLtQKlwjcwdOT8XuXMM"

URL_APP_SCRIPT = "https://script.google.com/macros/s/AKfycbyF5tbd1xTFlcxOSGuzwBboL4MoYxwbacN1jrQTSXa_oEr0IGVh2pKIHsnAwU-z040i_Q/exec"
TOKEN_SECRETO = "MI_CLAVE_SUPER_SECRETA_123"

# --- FUNCIONES DE COMUNICACIÓN CON DRIVE (TU CÓDIGO ORIGINAL SIN SIMPLIFICAR) ---

def get_gs_client():
    creds = Credentials.from_service_account_file(JSON_FILE, scopes=SCOPE)
    return gspread.authorize(creds)

def comunicar_con_drive(nombre_lote, accion="crear"):
    payload = {"token": TOKEN_SECRETO, "lote": str(nombre_lote), "accion": accion}
    try:
        respuesta = requests.post(URL_APP_SCRIPT, json=payload)
        return respuesta.text
    except Exception as e:
        return f"Error de conexión: {e}"

def preparar_hoja_lote(id_archivo, num_fracciones):
    if num_fracciones <= 1: return True
    try:
        client = get_gs_client()
        sh = client.open_by_key(id_archivo)
        ws = sh.worksheet("DATOS")
        cols_totales = num_fracciones * 8
        if ws.col_count < cols_totales:
            ws.add_cols(cols_totales - ws.col_count)
        sheet_id = ws._properties['sheetId']
        requests_list = []
        for i in range(1, num_fracciones):
            start_col = i * 8
            requests_list.append({
                "copyPaste": {
                    "source": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 8},
                    "destination": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": start_col, "endColumnIndex": start_col + 8},
                    "pasteType": "PASTE_NORMAL"
                }
            })
        if requests_list:
            sh.batch_update({"requests": requests_list})
        return True
    except Exception as e:
        st.error(f"Error organizando Excel: {e}")
        return False

def sincronizar_datos_lote(lote_n, lista_ids):
    try:
        client = get_gs_client()
        sh = client.open(str(lote_n))
        ws = sh.worksheet("DATOS")
        matriz = ws.get_all_values()
        for nid in lista_ids:
            num_f = int(st.session_state.lotes[nid]["meta_excel"][3])
            col_ini = (num_f - 1) * 8
            filas_encontradas = []
            for r in matriz[1:]:
                if len(r) > col_ini + 7 and r[col_ini+4]:
                    filas_encontradas.append({
                        "Fecha": r[col_ini+3], "Time": r[col_ini+4], 
                        "Estado": r[col_ini+5], "Proceso": r[col_ini+6], "Área": r[col_ini+7]
                    })
            st.session_state.lotes[nid]["datos"] = pd.DataFrame(filas_encontradas) if filas_encontradas else pd.DataFrame(columns=["Fecha", "Time", "Estado", "Proceso", "Área"])
    except: pass

def escribir_en_archivo_lote(lote_n, num_f, fila_datos):
    try:
        client = get_gs_client()
        sh = client.open(str(lote_n))
        ws = sh.worksheet("DATOS")
        c_idx = ((int(num_f) - 1) * 8) + 1
        col_vals = ws.col_values(c_idx)
        fila_dest = len(col_vals) + 1
        if fila_dest < 2: fila_dest = 2
        rango = f"{gspread.utils.rowcol_to_a1(fila_dest, c_idx)}:{gspread.utils.rowcol_to_a1(fila_dest, c_idx + 7)}"
        ws.update(rango, [fila_datos])
        return True
    except: return False

def eliminar_fila_en_archivo_lote(lote_n, num_f, index_fila):
    try:
        client = get_gs_client()
        sh = client.open(str(lote_n))
        ws = sh.worksheet("DATOS")
        sheet_id = ws._properties['sheetId']
        c_ini = (int(num_f) - 1) * 8
        c_fin = c_ini + 8
        r_idx = index_fila + 1 
        body = {
            "requests": [{
                "deleteRange": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": r_idx, "endRowIndex": r_idx + 1,
                        "startColumnIndex": c_ini, "endColumnIndex": c_fin
                    },
                    "shiftDimension": "ROWS"
                }
            }]
        }
        sh.batch_update(body)
        return True
    except: return False

# --- FUNCIONES DE SINCRONIZACIÓN CENTRAL (TU CÓDIGO ORIGINAL) ---

def sync_gs_to_local():
    try:
        client = get_gs_client()
        sh = client.open("0").worksheet("CATALOGO")
        data = sh.get_all_values()
        if len(data) > 1:
            rows = data[1:]
            st.session_state.maestros["productos"] = pd.DataFrame([r[0] for r in rows if r[0]], columns=["Nombre"])
            st.session_state.maestros["procesos"] = pd.DataFrame([r[1] for r in rows if len(r)>1 and r[1]], columns=["Nombre"])
            st.session_state.maestros["areas"] = pd.DataFrame([r[2] for r in rows if len(r)>2 and r[2]], columns=["Nombre"])
    except: pass

def save_local_to_gs():
    try:
        client = get_gs_client()
        sheet = client.open("0").worksheet("CATALOGO")
        p, r, a = st.session_state.maestros["productos"]["Nombre"].tolist(), st.session_state.maestros["procesos"]["Nombre"].tolist(), st.session_state.maestros["areas"]["Nombre"].tolist()
        vals = [[(p[i] if i<len(p) else ""), (r[i] if i<len(r) else ""), (a[i] if i<len(a) else "")] for i in range(max(len(p), len(r), len(a), 1))]
        sheet.batch_clear(["A2:C500"]); sheet.update("A2", vals)
    except: pass

def sync_desde_drive():
    try:
        client = get_gs_client()
        sheet = client.open_by_key(ID_REGISTROS).worksheet("FRACCIONES")
        data = sheet.get_all_values()
        if len(data) > 1:
            for r in data[1:]:
                nid = f"{r[0]} - {r[1]} ({r[2]})"
                if nid not in st.session_state.lotes:
                    st.session_state.lotes[nid] = {
                        "procesos": st.session_state.maestros["procesos"]["Nombre"].tolist(),
                        "areas": st.session_state.maestros["areas"]["Nombre"].tolist(),
                        "activo": False, "datos": pd.DataFrame(columns=["Fecha", "Time", "Estado", "Proceso", "Área"]),
                        "meta_excel": [r[0], r[1], r[2], r[3]]
                    }
    except: pass

def subir_a_drive():
    try:
        client = get_gs_client()
        sheet = client.open_by_key(ID_REGISTROS).worksheet("FRACCIONES")
        filas = [v["meta_excel"] for v in st.session_state.lotes.values() if "meta_excel" in v]
        sheet.batch_clear(["A2:D1000"])
        if filas: sheet.update("A2", filas)
    except: pass

def to_roman(n):
    val, syb = [10, 9, 5, 4, 1], ["X", "IX", "V", "IV", "I"]
    roman_num, i = "", 0
    while n > 0:
        for _ in range(n // val[i]): roman_num += syb[i]; n -= val[i]
        i += 1
    return roman_num

def generar_etiqueta_monitoreo(id_fraccion, info_fraccion):
    try:
        partes = id_fraccion.split(' - ')
        producto = partes[0]
        lote_y_frac = partes[1].split(' (')
        lote = lote_y_frac[0]
        fraccion_completa = lote_y_frac[1].rstrip(')')
        producto_corto = producto[:2].upper() if len(producto) >= 2 else producto.upper()
        lote_corto = lote[-2:] if len(lote) >= 2 else lote
        if '/' in fraccion_completa: fraccion_num = fraccion_completa.split('/')[0]
        elif '-' in fraccion_completa: fraccion_num = fraccion_completa.split(',')[0].split('-')[0] if ',' in fraccion_completa else fraccion_completa.split('-')[0]
        else: fraccion_num = fraccion_completa
        etiqueta_base = f"{producto_corto} | {lote_corto} | {fraccion_num}"
        datos = info_fraccion.get("datos", pd.DataFrame())
        if datos.empty or len(datos) == 0: return etiqueta_base
        ultimo = datos.iloc[-1]
        estado = ultimo.get("Estado", "")
        estado_emoji = estado.split()[0] if ' ' in estado else estado if estado else ""
        tiempo = ultimo.get("Time", "")
        proceso = ultimo.get("Proceso", "")
        proceso_corto = proceso[:2].upper() if len(proceso) >= 2 else proceso.upper() if proceso else ""
        return f"{etiqueta_base} | {estado_emoji} | {tiempo} | {proceso_corto}"
    except: return id_fraccion

# --- INICIALIZACIÓN (TU CÓDIGO ORIGINAL) ---
if 'maestros' not in st.session_state:
    st.session_state.maestros = {"productos": pd.DataFrame(columns=["Nombre"]), "procesos": pd.DataFrame(columns=["Nombre"]), "areas": pd.DataFrame(columns=["Nombre"])}
    sync_gs_to_local()
if 'lotes' not in st.session_state:
    st.session_state.lotes = {}
    sync_desde_drive()
if 'vista_activa' not in st.session_state: st.session_state.vista_activa = "📋 Catálogos"
if 'lote_seleccionado' not in st.session_state: st.session_state.lote_seleccionado = None
if 'hora_sugerida' not in st.session_state: st.session_state.hora_sugerida = datetime.now().strftime("%H:%M")

# --- NAVEGACIÓN (AÑADIDO BOTÓN GANTT) ---
st.markdown("### 🏭 Sistema de Control de Lotes")
c1, c2, c3, c4 = st.columns(4)
if c1.button("📋 Catálogos", use_container_width=True): st.session_state.vista_activa = "📋 Catálogos"; st.rerun()
if c2.button("📦 Planificar", use_container_width=True): st.session_state.vista_activa = "📦 Planificar"; st.rerun()
if c3.button("📊 Registro", use_container_width=True): st.session_state.vista_activa = "📊 Registro"; st.rerun()
if c4.button("📈 Gantt", use_container_width=True): st.session_state.vista_activa = "📈 Gantt"; st.rerun()
st.divider()

# --- PESTAÑAS (TU LÓGICA ORIGINAL) ---

if st.session_state.vista_activa == "📋 Catálogos":
    st.subheader("⚙️ Configuración de Maestros")
    if st.button("🔄 Sincronizar Catálogos", use_container_width=True): sync_gs_to_local(); st.rerun()
    cols = st.columns(3)
    cats = [("productos", cols[0], "📦 Productos"), ("procesos", cols[1], "⚙️ Procesos"), ("areas", cols[2], "📍 Áreas")]
    for clave, col, titulo in cats:
        with col:
            st.markdown(f"#### {titulo}")
            with st.form(f"f_{clave}", clear_on_submit=True):
                nuevo = st.text_input(f"Añadir {titulo[2:]}")
                if st.form_submit_button("Añadir"):
                    if nuevo:
                        st.session_state.maestros[clave] = pd.concat([st.session_state.maestros[clave], pd.DataFrame([{"Nombre": nuevo}])], ignore_index=True)
                        save_local_to_gs(); st.rerun()
            edited_df = st.data_editor(st.session_state.maestros[clave], num_rows="dynamic", use_container_width=True, key=f"ed_{clave}")
            if not edited_df.equals(st.session_state.maestros[clave]):
                st.session_state.maestros[clave] = edited_df; save_local_to_gs(); st.rerun()

elif st.session_state.vista_activa == "📦 Planificar":
    st.subheader("📋 Gestión de Lotes")
    if st.button("🔄 Actualizar desde Drive", use_container_width=True): sync_desde_drive(); st.rerun()
    if st.session_state.lotes:
        lotes_agrupados = {}
        for id_f, info in st.session_state.lotes.items():
            grupo_key = id_f.split(' (')[0]
            if grupo_key not in lotes_agrupados: lotes_agrupados[grupo_key] = []
            lotes_agrupados[grupo_key].append(id_f)
        for grupo, fracs_ids in lotes_agrupados.items():
            with st.expander(f"📦 Grupo: {grupo}"):
                c_del = st.columns([4, 1])[1]
                if c_del.button("🗑️", key=f"del_{grupo}"):
                    comunicar_con_drive(grupo.split(' - ')[1], "eliminar")
                    for f in fracs_ids: del st.session_state.lotes[f]
                    subir_a_drive(); st.rerun()
                df_g = pd.DataFrame([{"ID": f, "Fracción": f.split('(')[1][:-1], "Monitoreo": st.session_state.lotes[f]["activo"]} for f in fracs_ids])
                ed_g = st.data_editor(df_g, column_config={"Monitoreo": st.column_config.CheckboxColumn("Activar"), "ID": None}, disabled=["Fracción"], use_container_width=True, key=f"e_{grupo}")
                if st.button(f"💾 Guardar {grupo}", key=f"s_{grupo}"):
                    with st.status("Sincronizando 🔄") as status:
                        activas = [row["ID"] for _, row in ed_g.iterrows() if row["Monitoreo"]]
                        for _, row in ed_g.iterrows(): st.session_state.lotes[row["ID"]]["activo"] = row["Monitoreo"]
                        sincronizar_datos_lote(grupo.split(' - ')[1], activas); subir_a_drive()
                        status.update(label="✅ Sincronizado", state="complete")
                    st.rerun()
    st.divider(); st.subheader("🚀 Generar Nuevo Lote")
    m = st.session_state.maestros
    if not m["productos"].empty:
        with st.form("gen_lote", clear_on_submit=True):
            c1, c2 = st.columns(2)
            p_sel, l_val = c1.selectbox("Producto", m["productos"]["Nombre"]), c2.text_input("Número de Lote")
            c3, c4 = st.columns(2); f_cant = c3.number_input("Cantidad de Fracciones", 1, 50)
            f_tipo = c4.selectbox("Formato", [FORMATO_COMUN, FORMATO_ROMANO, FORMATO_PAREJAS])
            cp, ca = st.columns(2)
            p_ap = [p for p in m["procesos"]["Nombre"] if cp.checkbox(p, key=f"p_{p}")]
            a_ap = [a for a in m["areas"]["Nombre"] if ca.checkbox(a, key=f"a_{a}")]
            if st.form_submit_button("CREAR LOTE Y COPIAR BATCH", use_container_width=True):
                existentes = [id_f.split(' - ')[1].split(' (')[0] for id_f in st.session_state.lotes.keys()]
                if l_val in existentes: st.error(f"❌ El lote {l_val} ya existe.")
                elif l_val and p_ap and a_ap:
                    for i in range(1, int(f_cant) + 1):
                        tag = f"{i}/{int(f_cant)}" if f_tipo == FORMATO_COMUN else f"{to_roman(i)}/{to_roman(int(f_cant))}" if f_tipo == FORMATO_ROMANO else f"{(i-1)*2+1}-{((i-1)*2+1)+1}"
                        st.session_state.lotes[f"{p_sel} - {l_val} ({tag})"] = {"procesos": p_ap, "areas": a_ap, "activo": False, "datos": pd.DataFrame(columns=["Fecha", "Time", "Estado", "Proceso", "Área"]), "meta_excel": [p_sel, l_val, tag, i]}
                    subir_a_drive()
                    with st.spinner("Creando archivo..."):
                        res = comunicar_con_drive(l_val, "crear")
                        match = re.search(r"ID ([\w-]+)", res)
                        if match: preparar_hoja_lote(match.group(1), int(f_cant))
                    st.rerun()

elif st.session_state.vista_activa == "📊 Registro":
    with st.sidebar:
        st.header("Monitoreo")
        activos = {k: v for k, v in st.session_state.lotes.items() if v["activo"]}
        for k in activos:
            c_chk, c_btn = st.columns([0.8, 5])
            st.session_state[f"ck_{k}"] = c_chk.checkbox("", key=f"c_{k}", label_visibility="collapsed")
            etiqueta = generar_etiqueta_monitoreo(k, activos[k])
            if c_btn.button(etiqueta, use_container_width=True): st.session_state.lote_seleccionado = k
    if st.session_state.lote_seleccionado in st.session_state.lotes:
        id_act = st.session_state.lote_seleccionado
        st.subheader(f"📝 Registro: {id_act}")
        
        editor_key = f"h_{id_act}"
        edited_df = st.data_editor(st.session_state.lotes[id_act]["datos"], use_container_width=True, num_rows="dynamic", key=editor_key)
        
        if editor_key in st.session_state and st.session_state[editor_key].get("deleted_rows"):
            meta = st.session_state.lotes[id_act]["meta_excel"]
            filas_a_borrar = sorted(st.session_state[editor_key]["deleted_rows"], reverse=True)
            for idx in filas_a_borrar:
                eliminar_fila_en_archivo_lote(meta[1], meta[3], idx)
            st.session_state.lotes[id_act]["datos"] = edited_df
            st.rerun()
        else:
            st.session_state.lotes[id_act]["datos"] = edited_df

        c = st.columns([1.2, 1, 1.3, 1.8, 1.8, 0.6])
        f_v, h_v = c[0].date_input("F", label_visibility="collapsed"), c[1].text_input("H", value=st.session_state.hora_sugerida, label_visibility="collapsed")
        e_v, p_v, a_v = c[2].selectbox("E", ["⚪ PR", "🔴 ES", "🟢 OP", "🟡 IN"], label_visibility="collapsed"), c[3].selectbox("P", st.session_state.lotes[id_act]["procesos"], label_visibility="collapsed"), c[4].selectbox("A", st.session_state.lotes[id_act]["areas"], label_visibility="collapsed")
        if c[5].button("➕"):
            targets = [k for k in activos if st.session_state.get(f"ck_{k}")] or [id_act]
            for tid in targets:
                meta = st.session_state.lotes[tid]["meta_excel"]
                fila_ex = [meta[0], meta[1], meta[2], f_v.strftime("%d/%m"), h_v, e_v, p_v, a_v]
                if escribir_en_archivo_lote(meta[1], meta[3], fila_ex):
                    nueva = {"Fecha": f_v.strftime("%d/%m"), "Time": h_v, "Estado": e_v, "Proceso": p_v, "Área": a_v}
                    st.session_state.lotes[tid]["datos"] = pd.concat([st.session_state.lotes[tid]["datos"], pd.DataFrame([nueva])], ignore_index=True)
            st.rerun()

# --- PESTAÑA 4: GANTT INTELIGENTE (ADAPTACIÓN SOLICITADA) ---
elif st.session_state.vista_activa == "📈 Gantt":
    st.subheader("📈 Diagrama de Gantt Inteligente")
    with st.sidebar:
        st.header("Visualizar")
        gantt_targets = [k for k in st.session_state.lotes if st.checkbox(f"📊 {k}", key=f"gt_{k}")]
        if st.button("🔄 Actualizar Datos", use_container_width=True):
            for tid in gantt_targets:
                sincronizar_datos_lote(st.session_state.lotes[tid]["meta_excel"][1], [tid])
            st.rerun()

    if not gantt_targets:
        st.info("Selecciona fracciones para visualizar.")
    else:
        color_map = {"⚪ PR": "#FFFFFF", "🔴 ES": "#FF0000", "🟢 OP": "#7AC143", "🟡 IN": "#FFFF00"}
        for tid in gantt_targets:
            df = st.session_state.lotes[tid]["datos"].copy()
            if not df.empty:
                st.markdown(f"**🏷️ {tid}**")
                df['dt'] = pd.to_datetime(df['Fecha'] + f'/{datetime.now().year} ' + df['Time'], format='%d/%m/%Y %H:%M')
                df = df.sort_values('dt').reset_index(drop=True)
                
                g_data = []
                for cat in ["Estado", "Proceso", "Área"]:
                    current_val = None
                    start_time = None
                    for i in range(len(df)):
                        val = df.iloc[i][cat]
                        time_pt = df.iloc[i]['dt']
                        
                        # Lógica Inteligente: Solo crear nuevo cuadro si el valor CAMBIA
                        if val != current_val:
                            if current_val is not None:
                                g_data.append({"Eje": cat.upper(), "Inicio": start_time, "Fin": time_pt, "Valor": current_val})
                            current_val = val
                            start_time = time_pt
                        
                        # Al llegar al final, extender el cuadro actual
                        if i == len(df) - 1:
                            fin_pt = time_pt + timedelta(minutes=45)
                            g_data.append({"Eje": cat.upper(), "Inicio": start_time, "Fin": fin_pt, "Valor": current_val})

                df_p = pd.DataFrame(g_data)
                df_p['Texto'] = "<b>" + df_p['Valor'].astype(str) + "</b>"
                
                fig = px.timeline(df_p, x_start="Inicio", x_end="Fin", y="Eje", color="Valor", text="Texto",
                                  color_discrete_map=color_map, category_orders={"Eje": ["ESTADO", "PROCESO", "ÁREA"]})
                
                fig.update_traces(textposition='inside', insidetextanchor='middle', textfont=dict(size=14, color="black"),
                                  marker_line_color="black", marker_line_width=1.5)
                
                fig.update_layout(height=160, bargap=0.05, showlegend=False, margin=dict(l=0, r=10, t=5, b=5), plot_bgcolor="white")
                fig.update_yaxes(title="", tickfont=dict(size=11, family="Arial Black"))
                fig.update_xaxes(title="")
                
                st.plotly_chart(fig, use_container_width=True)
                st.divider()