import os, time, logging
logger = logging.getLogger(__name__)
EXPORTS_DIR = os.path.abspath("exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)

def _ts(): return str(int(time.time()))

def export_report(data, columns, fmt):
    fmt = fmt.lower().strip()
    if fmt in ("excel","xlsx"): return _excel(data,columns)
    if fmt == "pdf": return _pdf(data,columns)
    if fmt == "csv": return _csv(data,columns)
    raise ValueError(f"Unsupported format: {fmt}")

def _csv(data,columns):
    import csv
    path = os.path.join(EXPORTS_DIR,f"export_{_ts()}.csv")
    with open(path,"w",newline="",encoding="utf-8") as f:
        w = csv.DictWriter(f,fieldnames=columns); w.writeheader(); w.writerows(data)
    return path

def _excel(data,columns):
    try: import openpyxl; from openpyxl.styles import Font,PatternFill,Alignment,Border,Side
    except ImportError: raise ImportError("pip install openpyxl")
    wb=openpyxl.Workbook(); ws=wb.active; ws.title="Results"
    hf=Font(bold=True,color="FFFFFF"); hfill=PatternFill("solid",fgColor="1A56DB")
    thin=Side(style="thin",color="CBD5E1"); border=Border(left=thin,right=thin,top=thin,bottom=thin)
    for ci,col in enumerate(columns,1):
        cell=ws.cell(row=1,column=ci,value=col); cell.font=hf; cell.fill=hfill; cell.border=border
    alt=PatternFill("solid",fgColor="F0F5FF")
    for ri,row in enumerate(data,2):
        for ci,col in enumerate(columns,1):
            val=row.get(col); cell=ws.cell(row=ri,column=ci,value=val); cell.border=border
            if ri%2==0: cell.fill=alt
    for cc in ws.columns:
        ws.column_dimensions[cc[0].column_letter].width=min(max(len(str(c.value or "")) for c in cc)+4,40)
    path=os.path.join(EXPORTS_DIR,f"export_{_ts()}.xlsx"); wb.save(path); return path

def _pdf(data,columns):
    try:
        from reportlab.lib.pagesizes import landscape,A4; from reportlab.lib import colors
        from reportlab.lib.units import mm; from reportlab.platypus import SimpleDocTemplate,Table,TableStyle,Paragraph,Spacer
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError: raise ImportError("pip install reportlab")
    path=os.path.join(EXPORTS_DIR,f"export_{_ts()}.pdf")
    doc=SimpleDocTemplate(path,pagesize=landscape(A4),leftMargin=15*mm,rightMargin=15*mm,topMargin=15*mm,bottomMargin=15*mm)
    styles=getSampleStyleSheet(); story=[Paragraph("Query Results",styles["h1"]),Spacer(1,8*mm)]
    td=[columns]+[[str(row.get(c,"")) for c in columns] for row in data]
    cw=max((landscape(A4)[0]-30*mm)/len(columns) if columns else 20*mm, 20*mm)
    t=Table(td,colWidths=[cw]*len(columns),repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1A56DB")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,0),9),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#F0F5FF")]),("FONTSIZE",(0,1),(-1,-1),8),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#CBD5E1")),("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    story.append(t); doc.build(story); return path
