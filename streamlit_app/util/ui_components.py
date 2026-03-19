import streamlit as st

def render_page_header(title, icon=""):
    """Render a standard page header."""
    st.markdown(f'''
        <div class="flex items-center gap-3 mb-6 pb-4 border-b border-white/5">
            <span class="text-2xl">{icon}</span>
            <h1 class="text-2xl font-bold text-white m-0 p-0 leading-none">{title}</h1>
        </div>
    ''', unsafe_allow_html=True)

def render_metric_card(icon, label, value, color_class="text-white"):
    """Render a metric card."""
    # color_class e.g. text-cyan-400
    return f'''
    <div class="flex flex-col gap-2 p-5 rounded-xl bg-slate-800 border border-white/10 shadow-lg">
        <span class="text-2xl">{icon}</span>
        <span class="font-mono text-3xl font-bold {color_class}">{value}</span>
        <span class="text-xs font-semibold text-slate-400 uppercase tracking-widest">{label}</span>
    </div>
    '''

def render_badge(text, status="neutral"):
    """
    Render a status badge/pill.
    status: 'success', 'error', 'warning', 'info', 'neutral'
    """
    colors = {
        "success": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
        "error": "bg-rose-500/10 text-rose-400 border-rose-500/20",
        "warning": "bg-amber-500/10 text-amber-400 border-amber-500/20",
        "info": "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
        "neutral": "bg-slate-500/10 text-slate-400 border-slate-500/20"
    }
    c = colors.get(status, colors["neutral"])
    return f'<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border {c}">{text}</span>'

def render_provider_badge(provider_type, provider_label):
    colors = {
        "imou": "bg-blue-500/10 text-blue-400 border-blue-500/20", 
        "hikvision": "bg-rose-500/10 text-rose-400 border-rose-500/20", 
        "dahua": "bg-amber-500/10 text-amber-400 border-amber-500/20",
        "reolink": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20", 
        "tapo": "bg-purple-500/10 text-purple-400 border-purple-500/20", 
        "axis": "bg-pink-500/10 text-pink-400 border-pink-500/20",
        "rtsp": "bg-indigo-500/10 text-indigo-400 border-indigo-500/20", 
        "onvif": "bg-teal-500/10 text-teal-400 border-teal-500/20",
    }
    c = colors.get(provider_type, "bg-slate-500/10 text-slate-400 border-slate-500/20")
    return f'<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border {c}">{provider_label}</span>'

def render_table_header(col_specs):
    """
    col_specs: list of dicts [{'label': 'Tên', 'width': '180px'}, ...]
    """
    spans = []
    for c in col_specs:
        w = c.get('width', 'auto')
        align = c.get('align', 'left')
        spans.append(f'<div style="width:{w}; text-align:{align}" class="text-xs font-semibold text-slate-400 uppercase tracking-wider shrink-0">{c["label"]}</div>')
    
    html = f'''
    <div class="flex items-center p-3 mt-4 bg-slate-900 border-b border-white/10 rounded-t-xl gap-2">
        {"".join(spans)}
    </div>
    '''
    return html

def render_table_row(col_data, is_active=True, extra_classes=""):
    """
    col_data: list of dicts [{'content': 'Cam A', 'width': '180px'}, ...]
    """
    spans = []
    opacity = "opacity-100" if is_active else "opacity-50"
    for c in col_data:
        w = c.get('width', 'auto')
        align = c.get('align', 'left')
        spans.append(f'<div style="width:{w}; text-align:{align}" class="shrink-0">{c["content"]}</div>')
    
    html = f'''
    <div class="flex items-center p-3 border-b border-white/5 hover:bg-white/5 transition-colors gap-2 {opacity} {extra_classes}">
        {"".join(spans)}
    </div>
    '''
    return html

def get_status_dot(is_active):
    return "🟢" if is_active else "⚫"
