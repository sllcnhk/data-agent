"""
报告 API — /reports/*

POST   /reports/build                  从 spec 生成 HTML 报告
GET    /reports/{id}/refresh-data      重新查询数据（HTML 内刷新按钮调用，Bearer或token认证）
GET    /reports                        报告列表（分页）
GET    /reports/{id}                   报告详情
DELETE /reports/{id}                   删除报告 + 本地 HTML 文件
POST   /reports/{id}/export            异步导出 PDF/PPTX
GET    /reports/{id}/export-status     查询导出任务状态
GET    /reports/{id}/summary-status    查询 LLM 总结生成状态
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import secrets

from backend.api.deps import get_current_user, require_permission
from backend.core.auth.jwt import decode_token
from backend.config.database import get_db
from backend.config.settings import settings
from backend.models.report import Report
from backend.services.report_builder_service import (
    build_report_html,
    generate_llm_summary,
    generate_refresh_token,
)

router = APIRouter(prefix="/reports", tags=["报告"])
logger = logging.getLogger(__name__)

# ── Pilot FAB 注入片段 ────────────────────────────────────────────────────────
_PILOT_INJECT_TEMPLATE = """
<style>
  #__pilot-fab {
    position: fixed !important;
    bottom: 24px !important;
    right: 24px !important;
    width: 48px !important;
    height: 48px !important;
    border-radius: 50% !important;
    background: #52c41a !important;
    box-shadow: 0 4px 14px rgba(82,196,26,0.45) !important;
    cursor: pointer !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    z-index: 2147483647 !important;
    border: none !important;
    font-size: 22px !important;
    line-height: 1 !important;
    transition: transform 0.2s, box-shadow 0.2s !important;
  }
  #__pilot-fab:hover {
    transform: scale(1.12) !important;
    box-shadow: 0 6px 18px rgba(82,196,26,0.6) !important;
  }
  #__pilot-tip {
    position: fixed !important;
    bottom: 80px !important;
    right: 20px !important;
    background: rgba(0,0,0,0.72) !important;
    color: #fff !important;
    padding: 4px 10px !important;
    border-radius: 4px !important;
    font-size: 12px !important;
    z-index: 2147483647 !important;
    pointer-events: none !important;
    opacity: 0 !important;
    transition: opacity 0.2s !important;
    white-space: nowrap !important;
  }
</style>
<button id="__pilot-fab" title="AI Pilot">🤖</button>
<div id="__pilot-tip">AI 助手 Pilot</div>
<script>
(function(){
  var fab = document.getElementById('__pilot-fab');
  var tip = document.getElementById('__pilot-tip');
  if (!fab) return;

  // 安全读取 window 全局变量（_inject_chart_controls 中也有 sg，这里内联一份）
  function sg(n){try{return window[n];}catch(e){return undefined;}}

  // 父窗口可通过 postMessage 控制 FAB 显隐：
  //   {type:'pilotHideFab'} → 隐藏（预览 Modal 嵌入场景，由 React FAB 替代）
  //   {type:'pilotShowFab'} → 恢复显示
  window.addEventListener('message', function(e){
    if (!e.data) return;
    if (e.data.type === 'pilotHideFab') {
      fab.style.setProperty('display', 'none', 'important');
      if (tip) tip.style.setProperty('display', 'none', 'important');
    } else if (e.data.type === 'pilotShowFab') {
      fab.style.removeProperty('display');
      if (tip) tip.style.removeProperty('display');
    }
  });

  fab.addEventListener('mouseenter', function(){ tip.style.opacity = '1'; });
  fab.addEventListener('mouseleave', function(){ tip.style.opacity = '0'; });
  fab.addEventListener('click', function(){
    var rid = '__REPORT_ID_PLACEHOLDER__';
    var page = '__PAGE_PLACEHOLDER__';
    if (window !== window.top) {
      // 嵌入在 iframe 内（预览 Modal 或 ReportViewerPage）：通知父窗口打开 Pilot
      try { window.parent.postMessage({ type: 'openPilot', reportId: rid }, '*'); } catch(e) {}
    } else {
      // 独立标签页：导航到 /report-view 分屏页（左报表 + 右 Copilot）
      var tok = sg('REFRESH_TOKEN') || '';
      var docType = '__DOC_TYPE_PLACEHOLDER__';
      if (rid && tok) {
        window.location.href = window.location.origin
          + '/report-view?id=' + encodeURIComponent(rid)
          + '&token=' + encodeURIComponent(tok)
          + '&doc_type=' + docType;
      } else {
        // 兜底（无全局变量时）：沿用旧行为
        window.open(window.location.origin + '/data-center/' + page + '?autoPilot=' + rid, '_blank');
      }
    }
  });
})();
</script>
"""


def _inject_pilot_button(html_content: str, report_id: str, doc_type: str = "dashboard") -> str:
    """在 </body> 前注入悬浮 Pilot 按钮脚本，若无 </body> 则追加至末尾。"""
    page = "documents" if doc_type == "document" else "dashboards"
    doc_type_value = "document" if doc_type == "document" else "dashboard"
    snippet = (
        _PILOT_INJECT_TEMPLATE
        .replace("__REPORT_ID_PLACEHOLDER__", report_id)
        .replace("__PAGE_PLACEHOLDER__", page)
        .replace("__DOC_TYPE_PLACEHOLDER__", doc_type_value)
    )
    lower = html_content.lower()
    idx = lower.rfind("</body>")
    if idx >= 0:
        return html_content[:idx] + snippet + html_content[idx:]
    return html_content + snippet


# ── 图表控件注入（Chart Controls：⋮ kebab 菜单）────────────────────────────────
_CHART_CONTROLS_SNIPPET = """
<style id="__cc-style">
.cc-menu-btn{position:absolute!important;top:8px!important;right:8px!important;width:28px!important;height:28px!important;border:none!important;background:rgba(255,255,255,.88)!important;border-radius:50%!important;cursor:pointer!important;display:flex!important;align-items:center!important;justify-content:center!important;font-size:18px!important;font-weight:700!important;color:#595959!important;z-index:100!important;box-shadow:0 1px 4px rgba(0,0,0,.15)!important;padding:0!important;line-height:1!important;transition:background .15s!important}
.cc-menu-btn:hover{background:#fff!important;box-shadow:0 2px 8px rgba(0,0,0,.22)!important}
.cc-dropdown{position:absolute!important;top:40px!important;right:8px!important;background:#fff!important;border-radius:8px!important;box-shadow:0 4px 16px rgba(0,0,0,.15)!important;z-index:200!important;min-width:165px!important;padding:4px 0!important;display:none!important;border:1px solid #f0f0f0!important}
.cc-dropdown.cc-open{display:block!important}
.cc-menu-item{padding:8px 16px!important;font-size:13px!important;cursor:pointer!important;color:#262626!important;white-space:nowrap!important;transition:background .1s!important;user-select:none!important}
.cc-menu-item:hover{background:#f5f5f5!important}
.cc-menu-sep{height:1px!important;background:#f0f0f0!important;margin:4px 0!important}
.cc-modal-overlay{display:none;position:fixed!important;top:0!important;left:0!important;right:0!important;bottom:0!important;background:rgba(0,0,0,.5)!important;z-index:10000!important;align-items:center!important;justify-content:center!important}
.cc-modal-box{background:#fff!important;border-radius:8px!important;padding:24px!important;min-width:480px!important;max-width:720px!important;width:90vw!important;max-height:80vh!important;overflow-y:auto!important;box-shadow:0 8px 32px rgba(0,0,0,.2)!important}
.cc-modal-header{display:flex!important;justify-content:space-between!important;align-items:center!important;margin-bottom:4px!important}
.cc-modal-title{font-size:16px!important;font-weight:600!important;color:#262626!important}
.cc-modal-subtitle{font-size:12px!important;color:#8c8c8c!important;margin-bottom:16px!important}
.cc-modal-close{border:none!important;background:none!important;cursor:pointer!important;font-size:16px!important;color:#8c8c8c!important;padding:4px 8px!important;border-radius:4px!important}
.cc-modal-close:hover{color:#262626!important;background:#f5f5f5!important}
.cc-sql-block{background:#f6f8fa!important;border:1px solid #e8e8e8!important;border-radius:6px!important;padding:16px!important;font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace!important;font-size:13px!important;color:#24292e!important;overflow-x:auto!important;white-space:pre-wrap!important;word-break:break-all!important;max-height:320px!important;overflow-y:auto!important;margin:0!important}
.cc-modal-actions{margin-top:16px!important;display:flex!important;gap:8px!important}
.cc-copy-btn{padding:6px 14px!important;border:1px solid #d9d9d9!important;background:#fff!important;border-radius:4px!important;cursor:pointer!important;font-size:13px!important;transition:all .15s!important}
.cc-copy-btn:hover{border-color:#1677ff!important;color:#1677ff!important}
.chart-card:fullscreen,.chart-card:-webkit-full-screen,.chart-card:-moz-full-screen{background:#fff!important;padding:24px!important;box-sizing:border-box!important}
.chart-card:fullscreen .chart-container,.chart-card:-webkit-full-screen .chart-container,.chart-card:-moz-full-screen .chart-container{height:calc(100vh - 80px)!important}
</style>
<script id="__cc-script">
(function(){
'use strict';
if(document.getElementById('__cc-init'))return;
var _m=document.createElement('meta');_m.id='__cc-init';document.head.appendChild(_m);

function sg(n){try{return window[n];}catch(e){return undefined;}}

function _esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function triggerDl(blob,name){
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  a.href=url;a.download=name;
  document.body.appendChild(a);a.click();
  document.body.removeChild(a);
  setTimeout(function(){URL.revokeObjectURL(url);},1200);
}

function getChart(cid){
  var cs=sg('_charts')||{};
  return cs[cid]||(typeof echarts!=='undefined'&&echarts.getInstanceByDom(document.getElementById(cid)))||null;
}

function getChartSpec(cid){
  var rs=sg('REPORT_SPEC');
  return rs&&rs.charts&&rs.charts.find(function(c){return c.id===cid;})||null;
}

// ── T4: Force Refresh ─────────────────────────────────────────────────────────
window.ccForceRefresh=function(cid){
  var chart=getChart(cid);
  if(!chart)return;
  var rid=sg('REPORT_ID'),tok=sg('REFRESH_TOKEN'),base=sg('API_BASE');
  if(rid&&tok&&base){
    chart.showLoading({text:'刷新中\u2026',maskColor:'rgba(255,255,255,0.75)'});
    fetch(base+'/reports/'+rid+'/refresh-data?token='+encodeURIComponent(tok))
      .then(function(r){return r.json();})
      .then(function(json){
        chart.hideLoading();
        if(json.success&&json.data&&json.data[cid]){
          var nd=json.data[cid];
          if(window._chartData)window._chartData[cid]=nd;
          var sp=getChartSpec(cid);
          if(sp&&typeof buildEChartsOption==='function'){
            chart.setOption(buildEChartsOption(sp,nd),{notMerge:true});
          }else{var o=chart.getOption();chart.clear();chart.setOption(o);}
        }
      })
      .catch(function(){
        chart.hideLoading();
        var o=chart.getOption();chart.clear();chart.setOption(o);
      });
  }else{
    var o=chart.getOption();chart.clear();chart.setOption(o);
  }
};

// ── T5: Enter Fullscreen ──────────────────────────────────────────────────────
window.ccFullscreen=function(cid){
  var card=document.getElementById('card-'+cid);
  if(!card)return;
  var rfs=card.requestFullscreen||card.webkitRequestFullscreen||card.mozRequestFullScreen||card.msRequestFullscreen;
  if(!rfs){alert('当前浏览器不支持全屏');return;}
  rfs.call(card);
  function onFs(){var c=getChart(cid);if(c)setTimeout(function(){c.resize();},50);}
  document.addEventListener('fullscreenchange',onFs,{once:true});
  document.addEventListener('webkitfullscreenchange',onFs,{once:true});
  document.addEventListener('mozfullscreenchange',onFs,{once:true});
};

// ── T6: View Query ────────────────────────────────────────────────────────────
window.ccViewQuery=function(cid){
  var sp=getChartSpec(cid);
  var sql=sp&&sp.sql?sp.sql:'';
  var title=sp&&sp.title?sp.title:cid;
  var modal=document.getElementById('__cc-qm');
  if(!modal){
    modal=document.createElement('div');
    modal.id='__cc-qm';
    modal.className='cc-modal-overlay';
    modal.innerHTML=
      '<div class="cc-modal-box">'+
        '<div class="cc-modal-header">'+
          '<span class="cc-modal-title" id="__cc-qm-t"></span>'+
          '<button class="cc-modal-close" onclick="document.getElementById(\'__cc-qm\').style.display=\'none\'">&#10005;</button>'+
        '</div>'+
        '<div class="cc-modal-subtitle">View Query</div>'+
        '<pre class="cc-sql-block" id="__cc-qm-s"></pre>'+
        '<div class="cc-modal-actions">'+
          '<button class="cc-copy-btn" id="__cc-qm-c" onclick="ccCopySql()">&#128203; \u590d\u5236 SQL</button>'+
        '</div>'+
      '</div>';
    modal.addEventListener('click',function(e){if(e.target===modal)modal.style.display='none';});
    document.body.appendChild(modal);
  }
  document.getElementById('__cc-qm-t').textContent=title;
  var se=document.getElementById('__cc-qm-s');
  se.textContent=sql||'\u8be5\u56fe\u8868\u6682\u65e0\u67e5\u8be2\u8bed\u53e5';
  se.style.color=sql?'':'#999';
  modal.style.display='flex';
};

window.ccCopySql=function(){
  var sql=document.getElementById('__cc-qm-s').textContent;
  if(!sql||sql==='\u8be5\u56fe\u8868\u6682\u65e0\u67e5\u8be2\u8bed\u53e5')return;
  var btn=document.getElementById('__cc-qm-c');
  function ok(){if(btn){btn.innerHTML='\u2713 \u5df2\u590d\u5236';setTimeout(function(){btn.innerHTML='&#128203; \u590d\u5236 SQL';},2000);}}
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(sql).then(ok).catch(fallback);
  }else{fallback();}
  function fallback(){
    var ta=document.createElement('textarea');
    ta.value=sql;ta.style.position='fixed';ta.style.opacity='0';
    document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy');ok();}catch(e){}
    document.body.removeChild(ta);
  }
};

// ── T7: Download CSV / Excel ──────────────────────────────────────────────────
window.ccDownload=function(cid,fmt){
  var cd=sg('_chartData')||{};
  var rows=(cd[cid]||[]).slice();
  var sp=getChartSpec(cid);
  var title=sp&&sp.title?sp.title:cid;
  if(!rows.length){
    var chart=getChart(cid);
    if(chart){
      try{
        var opt=chart.getOption();
        var labels=(opt.xAxis&&opt.xAxis[0]&&opt.xAxis[0].data)||[];
        var series=opt.series||[];
        if(labels.length){
          rows=labels.map(function(lbl,i){
            var row={'\u7c7b\u522b':lbl};
            series.forEach(function(s){row[s.name||'value']=Array.isArray(s.data)?s.data[i]:'';});
            return row;
          });
        }
      }catch(e){}
    }
  }
  if(!rows.length){alert('\u6682\u65e0\u6570\u636e\u53ef\u4e0b\u8f7d');return;}
  var cols=Object.keys(rows[0]);
  var date=new Date().toISOString().slice(0,10);
  var fname=(title||cid).replace(/[^\\w\\u4e00-\\u9fff]/g,'_')+'_'+date;
  if(fmt==='csv'){
    var BOM='\\uFEFF';
    var lines=[cols.join(',')].concat(rows.map(function(r){
      return cols.map(function(c){
        var v=r[c]==null?'':String(r[c]);
        var needQ=v.indexOf(',')>=0||v.indexOf('"')>=0||v.indexOf('\\n')>=0;
        return needQ?'"'+v.replace(/"/g,'""')+'"':v;
      }).join(',');
    }));
    triggerDl(new Blob([BOM+lines.join('\\n')],{type:'text/csv;charset=utf-8;'}),fname+'.csv');
  }else{
    var th='<tr>'+cols.map(function(c){return'<th>'+_esc(c)+'</th>';}).join('')+'</tr>';
    var tb=rows.map(function(r){
      return'<tr>'+cols.map(function(c){return'<td>'+_esc(String(r[c]==null?'':r[c]))+'</td>';}).join('')+'</tr>';
    }).join('');
    triggerDl(
      new Blob(['<table border="1">'+th+tb+'</table>'],{type:'application/vnd.ms-excel;charset=utf-8;'}),
      fname+'.xls'
    );
  }
};

// ── T3: Init — attach menu to each .chart-card ────────────────────────────────
function addMenu(card,cid){
  if(card.querySelector('.cc-menu-btn'))return;
  card.style.position='relative';
  var btn=document.createElement('button');
  btn.className='cc-menu-btn';
  btn.innerHTML='&#8942;';
  btn.title='\u56fe\u8868\u64cd\u4f5c';
  var dd=document.createElement('div');
  dd.className='cc-dropdown';
  dd.innerHTML=
    '<div class="cc-menu-item" onclick="ccForceRefresh(\''+cid+'\')">&#10227; Force Refresh</div>'+
    '<div class="cc-menu-item" onclick="ccFullscreen(\''+cid+'\')">&#9645; Enter Fullscreen</div>'+
    '<div class="cc-menu-item" onclick="ccViewQuery(\''+cid+'\')">&#9776; View Query</div>'+
    '<div class="cc-menu-sep"></div>'+
    '<div class="cc-menu-item" onclick="ccDownload(\''+cid+'\',\'csv\')">&#8675; Download CSV</div>'+
    '<div class="cc-menu-item" onclick="ccDownload(\''+cid+'\',\'excel\')">&#8675; Download Excel</div>';
  btn.addEventListener('click',function(e){
    e.stopPropagation();
    document.querySelectorAll('.cc-dropdown.cc-open').forEach(function(m){if(m!==dd)m.classList.remove('cc-open');});
    dd.classList.toggle('cc-open');
  });
  card.appendChild(btn);
  card.appendChild(dd);
}

function initControls(){
  var seen={};
  // 优先：标准 .chart-card 结构（build_report_html 生成的报表）
  document.querySelectorAll('.chart-card').forEach(function(card){
    var cid=card.id.replace(/^card-/,'');
    if(cid){seen[cid]=true;addMenu(card,cid);}
  });
  // 兜底：老式手工 HTML，没有 .chart-card，改为查找带 ECharts 实例的容器
  if(typeof echarts!=='undefined'){
    document.querySelectorAll('[id]').forEach(function(el){
      var id=el.id;
      if(!id||seen[id])return;
      var inst=echarts.getInstanceByDom(el);
      if(!inst)return;
      // 优先使用父容器作为卡片包装层；若父容器没有 id，则直接用 echarts 容器本身
      var card=el.parentElement&&el.parentElement.id?el.parentElement:el;
      var cid=id;
      if(!seen[cid]){seen[cid]=true;addMenu(card,cid);}
    });
  }
}

document.addEventListener('click',function(){
  document.querySelectorAll('.cc-dropdown.cc-open').forEach(function(m){m.classList.remove('cc-open');});
});

if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',function(){setTimeout(initControls,250);});
}else{
  setTimeout(initControls,250);
}
})();
</script>
"""


def _inject_chart_controls(
    html_content: str,
    report_id: Optional[str] = None,
    refresh_token: Optional[str] = None,
) -> str:
    """
    在 </body> 前注入图表控件 CSS+JS（⋮ kebab 菜单：Force Refresh / Enter Fullscreen / View Query / Download）。
    若已注入（含 __cc-style 标记）则幂等跳过；无 </body> 则追加到末尾。

    report_id / refresh_token: 若提供，则先注入 __cc-vars 脚本覆盖 window.REPORT_ID 等全局变量，
    解决 Agent 直接写入的 HTML 文件缺少这些全局变量导致 Force Refresh 降级的问题。
    """
    if "__cc-style" in html_content:
        return html_content

    # 若 HTML 没有通过 window.REPORT_ID 或 var REPORT_ID 暴露变量（纯 Agent 写入的老式文件），
    # 注入 __cc-vars 覆盖脚本。build_report_html 生成的文件现在已显式设置 window.REPORT_ID，
    # __cc-vars 里的 if(!window.REPORT_ID) 守卫确保不会覆盖已有值，注入幂等安全。
    override_js = ""
    if report_id and "window.REPORT_ID" not in html_content and "var REPORT_ID=" not in html_content:
        safe_rid = str(report_id).replace('"', "").replace("'", "").replace("<", "").replace(">", "")
        safe_tok = str(refresh_token or "").replace('"', "").replace("'", "").replace("<", "").replace(">", "")
        override_js = (
            f'<script id="__cc-vars">'
            f'if(!window.REPORT_ID)window.REPORT_ID="{safe_rid}";'
            f'if(!window.REFRESH_TOKEN)window.REFRESH_TOKEN="{safe_tok}";'
            f'if(!window.API_BASE)window.API_BASE="/api/v1";'
            f'</script>\n'
        )

    snippet = override_js + _CHART_CONTROLS_SNIPPET
    lower = html_content.lower()
    idx = lower.rfind("</body>")
    if idx >= 0:
        return html_content[:idx] + snippet + html_content[idx:]
    return html_content + snippet


# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────
_CUSTOMER_DATA_ROOT: Path = (
    Path(settings.allowed_directories[0])
    if settings.allowed_directories
    else Path("customer_data")
)

# 内存中的导出任务状态（简单 KV，生产可换 Redis）
_export_jobs: Dict[str, Dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic 模型
# ─────────────────────────────────────────────────────────────────────────────

class BuildReportRequest(BaseModel):
    spec: Dict[str, Any] = Field(..., description="报告规格 JSON（见 ReportBuilderService 文档）")
    conversation_id: Optional[str] = Field(None, description="来源对话 ID")
    include_summary: bool = Field(False, description="是否异步生成 LLM 总结")
    doc_type: str = Field("dashboard", description="报告类型: dashboard | document")


class ExportReportRequest(BaseModel):
    format: str = Field("pdf", description="导出格式: pdf | pptx")


class UpdateSpecRequest(BaseModel):
    spec: Dict[str, Any] = Field(..., description="完整报告规格 JSON")


class UpdateChartRequest(BaseModel):
    chart: Dict[str, Any] = Field(
        ...,
        description="单个图表的配置（按 chart.id 合并到现有 spec，不影响其他图表）",
    )


class UpdateShareRequest(BaseModel):
    share_scope: str = Field("private", description="共享范围: public | team | private")
    allowed_users: List[str] = Field(default_factory=list, description="允许访问的用户名列表")


class CopilotRequest(BaseModel):
    title: Optional[str] = Field(None, description="对话标题（可选）")


# ─────────────────────────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────────────────────────

def _get_report_or_404(report_id: str, db: Session) -> Report:
    try:
        uid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的报告 ID")
    report = db.query(Report).filter(Report.id == uid).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")
    return report


def _check_ownership(report: Report, username: str, is_superadmin: bool = False) -> None:
    if is_superadmin:
        return  # superadmin can access all reports
    if report.username and report.username != username:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该报告")


def _report_dir(username: str) -> Path:
    d = _CUSTOMER_DATA_ROOT / username / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _api_base_url() -> str:
    """推断后端 API 前缀（供 HTML 内部刷新调用）。"""
    host = os.environ.get("PUBLIC_HOST", "")
    port = os.environ.get("PORT", "8000")
    if host:
        return f"{host}/api/v1"
    return f"http://localhost:{port}/api/v1"


# ─────────────────────────────────────────────────────────────────────────────
# 1. 构建报告 HTML
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/build")
async def build_report(
    req: BuildReportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "create")),
):
    """
    根据 spec 生成 HTML 报告文件，存入 customer_data/{username}/reports/，
    并在数据库中创建 Report 记录。

    如果 include_summary=true，则异步触发 LLM 总结生成。
    """
    username = getattr(current_user, "username", "default")
    spec = req.spec

    # 生成 UUID 和刷新令牌
    report_id = str(uuid.uuid4())
    refresh_token = generate_refresh_token()

    # 注入 include_summary 到 spec（供 HTML 渲染总结区域）
    spec["include_summary"] = req.include_summary

    # 生成 HTML
    try:
        html_content = build_report_html(
            spec=spec,
            report_id=report_id,
            refresh_token=refresh_token,
            api_base_url=_api_base_url(),
        )
    except Exception as e:
        logger.error("[Reports] HTML 生成失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"HTML 生成失败: {e}")

    # 写入文件
    report_dir = _report_dir(username)
    title_slug = _slugify(spec.get("title", "report"))[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{title_slug}_{ts}.html"
    html_path = report_dir / filename
    html_path.write_text(html_content, encoding="utf-8")

    # 相对路径（用于下载 API）
    try:
        rel_path = str(html_path.relative_to(_CUSTOMER_DATA_ROOT))
    except ValueError:
        rel_path = str(html_path)

    # 写入数据库
    conv_id = None
    if req.conversation_id:
        try:
            conv_id = uuid.UUID(req.conversation_id)
        except ValueError:
            pass

    report = Report(
        id=uuid.UUID(report_id),
        conversation_id=conv_id,
        name=spec.get("title", "数据报告"),
        description=spec.get("subtitle", ""),
        username=username,
        refresh_token=refresh_token,
        report_file_path=rel_path,
        summary_status="pending" if req.include_summary else "skipped",
        # 将原始 spec 中的 charts + data_sources 存入已有字段
        charts=spec.get("charts", []),
        data_sources=_build_data_sources(spec),
        filters=spec.get("filters", []),
        theme=spec.get("theme", "light"),
        extra_metadata={"spec_version": "1.0", "file_name": filename},
    )
    try:
        report.doc_type = req.doc_type
    except AttributeError:
        pass  # doc_type 列尚未迁移，跳过
    db.add(report)
    db.commit()
    db.refresh(report)

    # 异步生成 LLM 总结
    if req.include_summary:
        background_tasks.add_task(_async_generate_summary, report_id, spec, db)

    logger.info("[Reports] 报告已生成: id=%s, file=%s", report_id, rel_path)
    return {
        "success": True,
        "data": {
            "report_id": report_id,
            "file_path": rel_path,
            "file_name": filename,
            "refresh_token": refresh_token,
            "summary_status": report.summary_status,
        },
    }


def _build_data_sources(spec: Dict) -> List[Dict]:
    """从 charts 提取 data_sources 列表（存入 Report.data_sources）。"""
    sources = []
    seen = set()
    for c in spec.get("charts", []):
        key = (c.get("connection_env", ""), c.get("connection_type", "clickhouse"))
        if key not in seen and c.get("sql"):
            seen.add(key)
            sources.append({
                "id": c.get("id"),
                "type": c.get("connection_type", "clickhouse"),
                "env": c.get("connection_env", ""),
                "query": c.get("sql", ""),
            })
    return sources


def _slugify(s: str) -> str:
    import re
    s = s.strip().lower()
    s = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_") or "report"


# ─────────────────────────────────────────────────────────────────────────────
# 1b. 固定已生成的 HTML 文件为正式报表/报告（手动 pin）
# ─────────────────────────────────────────────────────────────────────────────

class PinReportRequest(BaseModel):
    file_path: str = Field(..., description="HTML 文件相对路径（相对于 customer_data/）")
    doc_type: str = Field("dashboard", description="报告类型: dashboard | document")
    name: Optional[str] = Field(None, description="报告名称（留空则从路径提取）")
    conversation_id: Optional[str] = Field(None, description="来源对话 ID")
    message_id: Optional[str] = Field(None, description="来源消息 ID（用于回写 pinned_report_id）")


@router.post("/pin")
async def pin_report(
    req: PinReportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "create")),
):
    """
    将对话中已生成的 HTML 文件固定为正式报表/报告，写入 reports 数据库。

    - 幂等：同一 file_path 已存在则直接返回已有记录（is_new=False）
    - 若传入 message_id，在消息 extra_metadata.files_written 中回写 pinned_report_id，
      支持页面刷新后按钮状态恢复
    """
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    # 路径安全检查：file_path 必须在 customer_data 目录内
    norm_fp = req.file_path.replace("\\", "/").lstrip("/")
    abs_path = (_CUSTOMER_DATA_ROOT / norm_fp).resolve()
    try:
        abs_path.relative_to(_CUSTOMER_DATA_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="文件路径超出允许范围")

    if not abs_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {req.file_path}")

    # 权限检查：非 superadmin 只能固定自己的文件
    # file_path 格式一般为 {username}/reports/xxx.html
    path_parts = norm_fp.split("/")
    if not is_superadmin and path_parts[0] != username:
        raise HTTPException(status_code=403, detail="无权固定其他用户的报告文件")

    # 幂等：检查是否已存在
    existing = db.query(Report).filter(Report.report_file_path == norm_fp).first()
    if existing:
        return {
            "success": True,
            "data": {
                "report_id": str(existing.id),
                "refresh_token": existing.refresh_token,
                "doc_type": getattr(existing, "doc_type", "dashboard"),
                "is_new": False,
            },
        }

    # 创建新记录
    report_id = str(uuid.uuid4())
    refresh_token = generate_refresh_token()
    file_name = norm_fp.split("/")[-1]
    name = req.name or file_name.rsplit(".", 1)[0]  # 去掉 .html 扩展名

    conv_id = None
    if req.conversation_id:
        try:
            conv_id = uuid.UUID(req.conversation_id)
        except ValueError:
            pass

    report = Report(
        id=uuid.UUID(report_id),
        conversation_id=conv_id,
        name=name,
        username=username,
        refresh_token=refresh_token,
        report_file_path=norm_fp,
        summary_status="skipped",
        extra_metadata={"pinned_from_chat": True, "file_name": file_name},
    )
    try:
        report.doc_type = req.doc_type
    except AttributeError:
        pass

    db.add(report)
    db.flush()  # 获取 ID，但不提交

    # 从 HTML 文件提取 spec，回填 charts / filters / theme
    # 这样通过 "生成固定报表" pin 的报告，Pilot 能立刻读到图表结构
    try:
        from backend.services.report_service import extract_spec_from_html_file
        from sqlalchemy.orm.attributes import flag_modified as _flag_modified
        _spec = extract_spec_from_html_file(abs_path)
        if _spec:
            report.charts = _spec.get("charts", [])
            report.filters = _spec.get("filters", [])
            report.theme = _spec.get("theme", "light")
            _flag_modified(report, "charts")
            _flag_modified(report, "filters")
            logger.info(
                "[Reports/pin] 从 HTML 提取 spec 成功: charts=%d, file=%s",
                len(report.charts or []), norm_fp,
            )
    except Exception as _e:
        logger.warning("[Reports/pin] 从 HTML 提取 spec 失败（非致命）: %s", _e)

    # T4: 回写 message.extra_metadata.files_written[i].pinned_report_id
    if req.message_id:
        try:
            from backend.models.conversation import Message
            msg_uid = uuid.UUID(req.message_id)
            msg = db.query(Message).filter(Message.id == msg_uid).first()
            if msg:
                meta = dict(msg.extra_metadata or {})
                files_list = list(meta.get("files_written", []))
                updated = False
                for f in files_list:
                    if f.get("path") == norm_fp or f.get("path") == req.file_path:
                        f["pinned_report_id"] = report_id
                        f["refresh_token"] = refresh_token
                        updated = True
                        break
                if updated:
                    meta["files_written"] = files_list
                    msg.extra_metadata = meta
                    try:
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(msg, "extra_metadata")
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("[Reports/pin] 回写 message metadata 失败（非致命）: %s", e)

    db.commit()
    db.refresh(report)

    logger.info(
        "[Reports/pin] 固定报告: id=%s, file=%s, doc_type=%s, user=%s",
        report_id, norm_fp, req.doc_type, username,
    )
    return {
        "success": True,
        "data": {
            "report_id": report_id,
            "refresh_token": refresh_token,
            "doc_type": req.doc_type,
            "is_new": True,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1c. 批量检查文件是否已固定（只读，不创建记录）
# ─────────────────────────────────────────────────────────────────────────────

class CheckPinStatusBatchRequest(BaseModel):
    file_paths: List[str] = Field(..., description="需要检查的 HTML 文件路径列表（相对于 customer_data/）")


@router.post("/check-pin-status-batch")
async def check_pin_status_batch(
    req: CheckPinStatusBatchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    批量检查多个文件是否已固定为报表/报告（只读，不创建记录）。

    - 非 superadmin 只能查询自己目录下的文件；对其他用户的文件静默返回 pinned=false
    - 路径越界的条目同样静默返回 pinned=false，不抛 4xx
    - 用于前端渲染文件卡片时的初始固定状态检测
    """
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)
    customer_data_root = _CUSTOMER_DATA_ROOT.resolve()

    # 归一化 + 安全/权限过滤
    safe_norm_paths: List[str] = []          # 通过检查的归一化路径
    skipped_originals: set = set()           # 原始路径 → 静默返回 pinned=false

    for fp in req.file_paths:
        norm_fp = fp.replace("\\", "/").lstrip("/")
        try:
            abs_path = (_CUSTOMER_DATA_ROOT / norm_fp).resolve()
            abs_path.relative_to(customer_data_root)
        except Exception:
            skipped_originals.add(fp)
            continue
        # 非 superadmin 只能查自己的文件（file_path 首段应为用户名）
        path_parts = norm_fp.split("/")
        if not is_superadmin and path_parts[0] != username:
            skipped_originals.add(fp)
            continue
        safe_norm_paths.append(norm_fp)

    # 批量查询 DB（一次 IN 查询）
    existing_map: Dict[str, Report] = {}
    if safe_norm_paths:
        rows = db.query(Report).filter(
            Report.report_file_path.in_(safe_norm_paths)
        ).all()
        for row in rows:
            if row.report_file_path:
                existing_map[row.report_file_path] = row

    # 构建结果列表
    results = []
    for fp in req.file_paths:
        if fp in skipped_originals:
            results.append({"file_path": fp, "pinned": False})
            continue
        norm_fp = fp.replace("\\", "/").lstrip("/")
        row = existing_map.get(norm_fp)
        if row:
            results.append({
                "file_path": fp,
                "pinned": True,
                "report_id": str(row.id),
                "refresh_token": row.refresh_token,
                "doc_type": getattr(row, "doc_type", "dashboard"),
                "name": row.name,
            })
        else:
            results.append({"file_path": fp, "pinned": False})

    return {"success": True, "data": {"results": results}}


async def _async_generate_summary(report_id: str, spec: Dict, db: Session) -> None:
    """后台任务：调用 LLM 生成总结并更新 DB + 本地 HTML 文件。"""
    try:
        from backend.agents.factory import get_default_llm_adapter
        llm_adapter = get_default_llm_adapter()

        # 更新状态为 generating
        rpt = db.query(Report).filter(Report.id == uuid.UUID(report_id)).first()
        if rpt:
            rpt.summary_status = "generating"
            db.commit()

        summary = await generate_llm_summary(spec, llm_adapter)

        if rpt:
            rpt.llm_summary = summary
            rpt.summary_status = "done"
            db.commit()

        # 同步更新 HTML 文件中的总结（简单替换占位符）
        if rpt and rpt.report_file_path and summary:
            html_path = _CUSTOMER_DATA_ROOT / rpt.report_file_path
            if html_path.exists():
                html = html_path.read_text(encoding="utf-8")
                html = html.replace(
                    "分析总结生成中，请稍候…",
                    summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
                    1,
                )
                html_path.write_text(html, encoding="utf-8")
        logger.info("[Reports] LLM 总结生成完成: report_id=%s", report_id)
    except Exception as e:
        logger.error("[Reports] LLM 总结生成失败: %s", e, exc_info=True)
        try:
            rpt = db.query(Report).filter(Report.id == uuid.UUID(report_id)).first()
            if rpt:
                rpt.summary_status = "failed"
                db.commit()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 2. 动态数据查询（参数化 SQL 渲染 + 执行）
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{report_id}/data")
async def get_report_data(
    report_id: str,
    token: str = Query(..., description="Report.refresh_token"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """
    执行报告中每个图表的参数化 SQL 查询，返回最新数据。

    认证方式：refresh_token（无需 JWT，适合 HTML 文件内直接调用）。

    SQL 参数通过 Query 字符串传递，例如：
        ?token=xxx&date_start=2025-01-01&date_end=2025-12-31&enterprise_id=123

    参数名需与图表 SQL 模板中的 {{ variable }} 名称一致。
    若不传参数，自动使用 spec.filters 中 default_days / default_value 计算默认值。
    """
    from backend.services.report_params_service import (
        render_sql,
        extract_default_params,
        flatten_query_params,
    )

    try:
        uid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(400, "无效的报告 ID")

    report = db.query(Report).filter(Report.id == uid).first()
    if not report:
        raise HTTPException(404, "报告不存在")
    if not secrets.compare_digest(report.refresh_token or "", token):
        raise HTTPException(403, "无效的刷新令牌")

    # 提取 SQL 参数（除 token 外所有 query 参数）
    raw_params: Dict[str, Any] = {}
    if request is not None:
        raw_params = {
            k: (v if len(v) > 1 else v[0])
            for k, v in request.query_params.multi_items()
            if k != "token"
        }

    # 若无运行时参数，使用 spec 默认值
    spec_for_defaults = {
        "filters": report.filters or [],
        "charts": report.charts or [],
    }
    if not raw_params:
        raw_params = extract_default_params(spec_for_defaults)

    # 逐图表渲染 SQL 并执行
    new_data: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    charts = report.charts or []

    for chart in charts:
        cid = chart.get("id")
        sql_template = chart.get("sql", "")
        env = chart.get("connection_env", "")
        conn_type = chart.get("connection_type", "clickhouse")
        if not sql_template or not env:
            continue
        try:
            rendered_sql = render_sql(sql_template, raw_params)
            rows = await _run_query(rendered_sql, env, conn_type)
            new_data[cid] = rows
        except Exception as exc:
            errors[cid] = str(exc)
            logger.warning("[Reports/data] 查询失败 chart=%s: %s", cid, exc)

    # 更新 view_count
    report.increment_view_count()
    db.commit()

    return {
        "success": True,
        "data": new_data,
        "errors": errors,
        "params_used": raw_params,
        "llm_summary": report.llm_summary,
        "refreshed_at": datetime.utcnow().isoformat(),
    }


@router.get("/{report_id}/refresh-data")
async def refresh_report_data(
    report_id: str,
    token: str = Query(..., description="Report.refresh_token"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """
    兼容旧版刷新接口，委托给 /data 端点（已有 HTML 文件的向后兼容）。
    """
    return await get_report_data(
        report_id=report_id,
        token=token,
        request=request,
        db=db,
    )


@router.post("/{report_id}/regenerate-html")
async def regenerate_report_html(
    report_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "create")),
):
    """
    用最新 HTML 模板重新生成报表文件，并覆盖原文件。

    适用场景：修复已有报表因旧版模板生成导致的 binds 格式错误、
    筛选器不生效等问题，无需重新对话创建报表。

    认证：需要 reports:create 权限；非 superadmin 只能重新生成自己的报表。
    """
    from backend.services.report_params_service import _normalize_binds as _nb

    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, is_superadmin)

    if not report.report_file_path:
        raise HTTPException(status_code=400, detail="该报表无关联 HTML 文件，无法重新生成")

    # 从 DB 中取 spec，归一化 binds
    raw_filters = report.filters or []
    normalized_filters = []
    for _f in raw_filters:
        _f2 = dict(_f)
        if "binds" in _f2:
            _f2["binds"] = _nb(_f2["binds"])
        normalized_filters.append(_f2)

    spec_for_html = {
        "title": report.name or "分析报告",
        "subtitle": report.description or "",
        "theme": report.theme or "light",
        "charts": report.charts or [],
        "filters": normalized_filters,
    }

    try:
        html_content = build_report_html(
            spec=spec_for_html,
            report_id=report_id,
            refresh_token=report.refresh_token or "",
            api_base_url=_api_base_url(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HTML 重新生成失败: {e}")

    html_path = (_CUSTOMER_DATA_ROOT / report.report_file_path).resolve()
    try:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_content, encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HTML 文件写入失败: {e}")

    # 同步更新 DB 中存储的 filters（归一化后）
    report.filters = normalized_filters
    db.commit()

    return {
        "success": True,
        "report_id": report_id,
        "html_path": str(report.report_file_path),
        "message": "HTML 文件已用最新模板重新生成，filters.binds 已归一化",
    }


# 模块级 ClickHouse 客户端缓存（env → client 实例，避免重复 TCP/HTTP 初始化）
_ch_client_cache: Dict[str, Any] = {}


async def _get_or_init_ch_client(env: str) -> Any:
    """
    获取或初始化指定 env 的 ClickHouse 客户端（带模块级缓存）。

    优先使用 TCP（native）连接，失败自动回退 HTTP，逻辑与 ClickHouseMCPServer.initialize() 一致。

    connection_env 兼容处理：AI 可能传 "clickhouse-sg" 而非 "sg"，此处自动去掉前缀。
    """
    # 兼容 AI 传入 "clickhouse-sg" / "clickhouse-sg-azure" 等带前缀格式
    if env.startswith("clickhouse-"):
        env = env[len("clickhouse-"):]
    if env not in _ch_client_cache:
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        srv = ClickHouseMCPServer(env=env)
        await srv.initialize()
        _ch_client_cache[env] = srv.client
    return _ch_client_cache[env]


async def _run_query(sql: str, env: str, conn_type: str = "clickhouse") -> List[Dict]:
    """执行查询并返回行列表（dict 格式）。"""
    if conn_type == "clickhouse":
        client = await _get_or_init_ch_client(env)
        if hasattr(client, "execute"):
            rows, cols = client.execute(sql, with_column_types=True)
            col_names = [c[0] for c in cols]
            return [dict(zip(col_names, row)) for row in rows]
        else:
            # HTTP client
            result = client.execute(sql)
            return result if isinstance(result, list) else []
    elif conn_type == "mysql":
        from backend.mcp.mysql.server import get_mysql_client
        conn = get_mysql_client(env)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql)
        return cursor.fetchall()
    return []


# ─────────────────────────────────────────────────────────────────────────────
# 3. 报告列表 & 详情 & 删除
# ─────────────────────────────────────────────────────────────────────────────

@router.get("")
async def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    doc_type: Optional[str] = Query(None, description="过滤类型: dashboard | document"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    q = db.query(Report)
    if not is_superadmin:
        q = q.filter(Report.username == username)
    if doc_type is not None:
        try:
            q = q.filter(Report.doc_type == doc_type)
        except AttributeError:
            pass  # doc_type 列尚未迁移，跳过过滤
    total = q.count()
    reports = q.order_by(Report.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return {
        "success": True,
        "data": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [_report_to_dict(r) for r in reports],
        },
    }


@router.get("/html-serve")
async def serve_report_html_by_path(
    path: str = Query(..., description="文件路径（含或不含 customer_data/ 前缀均可）"),
    token: str = Query("", description="JWT access token（iframe 场景下无法发 Authorization header，以此替代）"),
    db: Session = Depends(get_db),
):
    """
    为 iframe 预览提供的报告 HTML 服务端点（JWT 以 query param 方式鉴权）。

    浏览器加载 iframe src 时不发送自定义 Authorization header，因此以 query param
    传递 JWT 进行验证。路径必须属于当前用户的 customer_data/{username}/ 目录。

    ENABLE_AUTH=false 时跳过 token 验证（单用户模式）。
    """
    is_superadmin = False
    if settings.enable_auth:
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录，请先登录")
        payload = decode_token(token, settings.jwt_secret, settings.jwt_algorithm)
        if not payload:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效或已过期")

        from backend.models.user import User
        user_id = payload.get("sub")
        user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")

        username = user.username
        is_superadmin = getattr(user, "is_superadmin", False)
    else:
        username = "default"

    # 解析路径（兼容含/不含 customer_data/ 前缀两种格式）
    normalized = path.replace("\\", "/")
    customer_data_name = _CUSTOMER_DATA_ROOT.name
    if normalized.startswith(customer_data_name + "/"):
        rel = normalized[len(customer_data_name) + 1:]
    else:
        rel = normalized

    abs_path = (_CUSTOMER_DATA_ROOT / rel).resolve()

    # 所有权验证：文件必须在 customer_data/{username}/ 下
    # superadmin 豁免：可访问 customer_data/ 下任意文件（用于调试和紧急救援）
    if settings.enable_auth and not is_superadmin:
        user_root = (_CUSTOMER_DATA_ROOT / username).resolve()
        try:
            abs_path.relative_to(user_root)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问此文件")
    elif settings.enable_auth and is_superadmin:
        # superadmin 仍然要求文件在 customer_data/ 内（防止路径穿越）
        try:
            abs_path.relative_to(_CUSTOMER_DATA_ROOT.resolve())
        except ValueError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问此文件")

    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")

    try:
        content = abs_path.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件读取失败: {e}")

    content = _inject_chart_controls(content)
    return HTMLResponse(content=content)


@router.get("/{report_id}/html")
async def serve_report_html_by_token(
    report_id: str,
    token: str = Query(..., description="Report.refresh_token（无需登录）"),
    download: bool = Query(False, description="True 时以附件方式下载而非内嵌预览"),
    db: Session = Depends(get_db),
):
    """
    通过 refresh_token 访问报告 HTML（无需 JWT）。

    适用场景：
    - 报告列表页「预览」按钮（refresh_token 来自 GET /reports 响应）
    - 报告列表页「下载 HTML」按钮（download=true）
    - 将报告嵌入邮件/文档，允许无账号人员访问
    """
    try:
        uid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的报告 ID")

    report = db.query(Report).filter(Report.id == uid).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")

    # Public reports: skip token check
    try:
        _share_scope_val = report.share_scope
        if hasattr(_share_scope_val, "value"):
            _share_scope_val = _share_scope_val.value
        if _share_scope_val and _share_scope_val == "public":
            pass  # Public access — no token needed, fall through to file serving
        elif not secrets.compare_digest(report.refresh_token or "", token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无效的刷新令牌")
    except AttributeError:
        if not secrets.compare_digest(report.refresh_token or "", token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无效的刷新令牌")

    if not report.report_file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告文件路径未记录")

    file_path = (_CUSTOMER_DATA_ROOT / report.report_file_path).resolve()
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告文件不存在（可能已被删除）")

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件读取失败: {e}")

    headers = {}
    if download:
        filename = file_path.name
        import urllib.parse
        encoded = urllib.parse.quote(filename)
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded}"
    else:
        # 非下载模式：注入图表控件 + 悬浮 Pilot 按钮（按 doc_type 路由到正确页面）
        content = _inject_chart_controls(content, report_id=report_id, refresh_token=report.refresh_token)
        doc_type_val = getattr(report.doc_type, "value", report.doc_type) or "dashboard"
        content = _inject_pilot_button(content, report_id, doc_type=str(doc_type_val))

    return HTMLResponse(content=content, headers=headers)


@router.put("/{report_id}/spec")
async def update_report_spec(
    report_id: str,
    req: UpdateSpecRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "create")),
):
    """
    用新的 spec 重新生成报告 HTML，并更新数据库中的 charts/filters/theme 等字段。
    """
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    try:
        html_content = build_report_html(
            spec=req.spec,
            report_id=str(report.id),
            refresh_token=report.refresh_token,
            api_base_url=_api_base_url(),
        )
    except Exception as e:
        logger.error("[Reports] spec 更新 HTML 生成失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"HTML 生成失败: {e}")

    if not report.report_file_path:
        raise HTTPException(status_code=400, detail="报告尚无 HTML 文件路径，无法覆写")

    html_path = _CUSTOMER_DATA_ROOT / report.report_file_path
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_content, encoding="utf-8")

    # 更新数据库字段
    report.charts = req.spec.get("charts", report.charts)
    report.data_sources = _build_data_sources(req.spec) or report.data_sources
    report.filters = req.spec.get("filters", report.filters)
    report.theme = req.spec.get("theme", report.theme)
    if req.spec.get("title"):
        report.name = req.spec["title"]
    db.commit()

    logger.info("[Reports] spec 更新完成: id=%s", report_id)
    return {
        "success": True,
        "data": {
            "report_id": str(report.id),
            "updated_at": datetime.utcnow().isoformat(),
            "spec_updated": True,
        },
    }


@router.put("/{report_id}/charts/{chart_id}")
async def update_single_chart(
    report_id: str,
    chart_id: str,
    req: UpdateChartRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "create")),
):
    """
    局部更新报告中单个图表的配置。

    按 chart_id（路径参数 或 req.chart["id"]）将传入的字段 merge 到现有图表，
    不影响报告中其他图表。适用于 AI Pilot 单图修改场景，避免全量替换 spec 导致
    其他图表被意外删除（RC2 修复）。

    匹配规则：
    - 优先用路径参数 chart_id 匹配；
    - 若 req.chart["id"] 存在且与路径参数不同，以路径参数为准。
    - 若 chart_id 在现有图表中不存在，则追加为新图表。
    """
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    existing_charts: List[Dict[str, Any]] = list(report.charts or [])

    # 确保传入 chart 的 id 与路径参数一致
    incoming = dict(req.chart)
    incoming["id"] = chart_id  # 路径参数优先

    found = False
    merged_charts: List[Dict[str, Any]] = []
    for c in existing_charts:
        if c.get("id") == chart_id:
            # Merge：以原图表为基础，用传入字段覆盖（浅合并）
            merged_charts.append({**c, **incoming})
            found = True
        else:
            merged_charts.append(c)

    if not found:
        # chart_id 不存在于现有图表：作为新图表追加
        merged_charts.append(incoming)

    # 从 DB 已有字段重建完整 spec（保留 title/theme/filters/data_sources）
    merged_spec: Dict[str, Any] = {
        "title": report.name,
        "subtitle": report.description or "",
        "theme": report.theme or "light",
        "filters": report.filters or [],
        "data_sources": report.data_sources or [],
        "charts": merged_charts,
        "include_summary": False,
        "data": {},
    }

    try:
        html_content = build_report_html(
            spec=merged_spec,
            report_id=str(report.id),
            refresh_token=report.refresh_token,
            api_base_url=_api_base_url(),
        )
    except Exception as e:
        logger.error("[Reports] 单图更新 HTML 生成失败: report_id=%s chart_id=%s err=%s",
                     report_id, chart_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"HTML 生成失败: {e}")

    if not report.report_file_path:
        raise HTTPException(status_code=400, detail="报告尚无 HTML 文件路径，无法覆写")

    html_path = _CUSTOMER_DATA_ROOT / report.report_file_path
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_content, encoding="utf-8")

    report.charts = merged_charts
    db.commit()

    logger.info("[Reports] 单图更新完成: report_id=%s chart_id=%s found=%s", report_id, chart_id, found)
    return {
        "success": True,
        "data": {
            "report_id": str(report.id),
            "chart_id": chart_id,
            "found": found,
            "total_charts": len(merged_charts),
            "updated_at": datetime.utcnow().isoformat(),
            "spec_updated": True,
        },
    }


@router.put("/{report_id}/share")
async def update_report_share(
    report_id: str,
    req: UpdateShareRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "create")),
):
    """
    更新报告的共享范围（public / team / private）和允许访问的用户列表。
    """
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    try:
        report.share_scope = req.share_scope
        report.allowed_users = req.allowed_users
        db.commit()
    except AttributeError:
        # share_scope / allowed_users 列尚未迁移，跳过
        pass

    return {"success": True}


@router.post("/{report_id}/copilot")
async def create_report_copilot(
    report_id: str,
    req: CopilotRequest = CopilotRequest(),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    为指定报告获取或创建 Co-pilot 对话（upsert）。

    同一用户对同一报表只使用一个 Pilot 对话，不会重复创建。
    返回 conversation_id 以及 created 标识（true=新建, false=复用已有）。
    """
    from backend.services.conversation_service import ConversationService

    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    user_id = getattr(current_user, "id", None)
    if user_id is not None and str(user_id) == "default":
        user_id = None

    svc = ConversationService(db)

    # ── C1: 若 charts 为空，先尝试从 HTML 文件提取 spec ───────────────────
    _spec_extracted = False
    if not report.charts and report.report_file_path:
        try:
            from backend.services.report_service import extract_spec_from_html_file
            from sqlalchemy.orm.attributes import flag_modified as _flag_modified
            _abs = (_CUSTOMER_DATA_ROOT / report.report_file_path).resolve()
            _spec = extract_spec_from_html_file(_abs)
            if _spec and _spec.get("charts"):
                report.charts = _spec.get("charts", [])
                report.filters = _spec.get("filters", []) or (report.filters or [])
                report.theme = _spec.get("theme", report.theme or "light")
                _flag_modified(report, "charts")
                _flag_modified(report, "filters")
                db.commit()
                db.refresh(report)
                _spec_extracted = True
                logger.info(
                    "[Reports/copilot] 从 HTML 提取 spec 成功: report_id=%s, charts=%d",
                    report_id, len(report.charts or []),
                )
        except Exception as _e:
            logger.warning("[Reports/copilot] 从 HTML 提取 spec 失败（非致命）: %s", _e)

    charts_list = report.charts or []

    # ── C2: 仍为空时注入 HTML 文本摘要，给 Pilot 兜底上下文 ───────────────
    _html_context_note = ""
    if not charts_list and report.report_file_path:
        try:
            from backend.services.report_service import extract_html_context
            _abs = (_CUSTOMER_DATA_ROOT / report.report_file_path).resolve()
            _html_ctx = extract_html_context(_abs, max_chars=1500)
            if _html_ctx:
                _html_context_note = (
                    "\n## 报表 HTML 内容摘要（spec 提取失败时的兜底上下文）\n"
                    "注意：此报表为自由格式 HTML，图表配置未结构化存储。\n"
                    "以下是 HTML 可读文本，仅供理解报表内容，不可直接编辑图表数据。\n"
                    f"{_html_ctx}\n"
                    "⚠️ 如需修改此报表，请告知用户「该报表为自由格式，Pilot 无法直接编辑图表数据，"
                    "建议重新生成结构化报表。」\n"
                )
        except Exception:
            pass

    # chart_summary 在 _build_copilot_prompt 内部动态生成（含 ★参数化 标注）

    # 计算报表当前默认参数（供 Pilot 了解实际查询范围）
    _default_params_str = ""
    try:
        from backend.services.report_params_service import extract_default_params
        _dp = extract_default_params({"filters": report.filters or []})
        if _dp:
            _default_params_str = json.dumps(_dp, ensure_ascii=False)
    except Exception:
        pass

    # 检测是否有参数化 SQL（含 {{ }} 模板变量）
    _has_param_sql = any(
        "{{" in (c.get("sql") or "")
        for c in (charts_list or [])
    )

    def _build_copilot_prompt(cl: list, note: str) -> str:
        # 为参数化图表在列表中加 ★ 标注
        _chart_summary_lines = []
        for i, c in enumerate(cl):
            sql_tag = " ★参数化" if "{{" in (c.get("sql") or "") else ""
            _chart_summary_lines.append(
                f'  [{i+1}] id="{c.get("id","?")}" '
                f'title="{c.get("title","?")}" '
                f'type="{c.get("chart_type","?")}"'
                f'{sql_tag}'
            )
        _chart_summary_with_tags = "\n".join(_chart_summary_lines) or "  （无图表）"

        param_sql_section = ""
        if _has_param_sql and _default_params_str:
            param_sql_section = (
                f"\n## 参数化查询说明\n"
                f"当前默认参数（由筛选器 default_days/default_value 推导）：\n"
                f"  {_default_params_str}\n\n"
                "SQL 模板语法（Jinja2）：\n"
                "  图表 SQL 中使用 `{{ param_name }}` 引用筛选器绑定的参数变量。\n"
                "  例：`WHERE call_start_time >= '{{ date_start }}' AND call_start_time < '{{ date_end }}'`\n"
                "  筛选器 binds 字段定义映射：date_range → {\"start\": \"date_start\", \"end\": \"date_end\"}\n\n"
                "⚠️ 修改注意事项：\n"
                "  - 修改时间范围默认值 → 改 filter 的 `default_days` 或 `default_value`，不要在 SQL 中硬编码日期\n"
                "  - 新增筛选维度 → 在 filter spec 中添加 binds，并在 SQL 中添加对应 `{{ variable }}` 条件\n"
                "  - 禁止把参数化 SQL 改回硬编码日期（会导致报表刷新时查询范围无法变化）\n"
            )

        return (
            f"[Co-pilot 模式] 当前报表：{report.name}\n"
            f"report_id：{report.id}\n"
            f"refresh_token：{report.refresh_token}\n"
            f"图表数量：{len(cl)}\n"
            f"图表列表：\n{_chart_summary_with_tags}\n"
            f"图表配置（详细）：{json.dumps(cl, ensure_ascii=False)[:4000]}\n"
            f"过滤器配置（含参数绑定）：{json.dumps(report.filters or [], ensure_ascii=False)[:600]}\n"
            f"主题：{report.theme}\n"
            f"{param_sql_section}\n"
            "## 修改报表的 MCP 工具（直接调用，无需 JWT）\n\n"
            "### 工具 1：report__get_spec\n"
            "读取报表当前完整 spec，修改前先调用确认所有图表。\n"
            "参数：report_id（见上方）、token（即 refresh_token）\n\n"
            "### 工具 2：report__update_single_chart（优先使用）\n"
            "局部更新单个图表，merge 操作，不影响其他图表。\n"
            "参数：report_id、token、chart_id、chart_patch（只含需修改字段）\n\n"
            "### 工具 3：report__update_spec（全量更新）\n"
            "更新整个报表 spec，适用于添加/删除图表、修改筛选器或批量修改。\n"
            "⚠️ spec.charts 必须包含所有图表，缺少的图表将永久删除！\n"
            "参数：report_id、token、spec（完整 spec JSON 含所有图表和 filters）\n\n"
            f"{note}"
            "请基于以上报表信息协助用户修改报表。修改完成后告知用户「报表已更新，请查看预览」。"
        )

    copilot_system_prompt = _build_copilot_prompt(charts_list, _html_context_note)

    # ── Upsert：先查已有对话，避免重复创建 ──────────────────────────────────
    existing = svc.find_pilot_conversation(
        context_type="report",
        context_id=str(report.id),
        user_id=user_id,
    )
    if existing:
        # ── C3: 若已有对话 system_prompt 中图表数量为 0 但现在有了 spec，刷新 prompt ──
        _pilot_refreshed = False
        try:
            _old_prompt = existing.system_prompt or ""
            _old_has_empty = "图表数量：0" in _old_prompt or "（无图表）" in _old_prompt
            if _old_has_empty and charts_list:
                from sqlalchemy.orm.attributes import flag_modified as _flag_modified2
                _meta = dict(existing.extra_metadata or {})
                _meta["system_prompt"] = copilot_system_prompt
                existing.extra_metadata = _meta
                _flag_modified2(existing, "extra_metadata")
                db.commit()
                _pilot_refreshed = True
                logger.info(
                    "[Reports/copilot] 刷新 stale system_prompt: conv_id=%s, charts=%d",
                    existing.id, len(charts_list),
                )
        except Exception as _re:
            logger.warning("[Reports/copilot] 刷新 system_prompt 失败（非致命）: %s", _re)

        logger.info(
            "[Reports] co-pilot 对话已存在，复用: report_id=%s, conv_id=%s, refreshed=%s",
            report_id, existing.id, _pilot_refreshed,
        )
        return {
            "success": True,
            "data": {
                "conversation_id": str(existing.id),
                "created": False,
                "pilot_refreshed": _pilot_refreshed,
            },
        }

    # 优先用 HTML 文件名（无扩展名）作为标题，让 Chat 首页能识别"这是哪个报表的 Pilot"
    _title_stem = ""
    if report.report_file_path:
        from pathlib import Path as _TitlePath
        _title_stem = _TitlePath(report.report_file_path).stem
    _title_display = _title_stem or report.name
    conv_title = req.title or f"{_title_display} · Pilot"
    conv = svc.create_conversation(
        title=conv_title,
        system_prompt=copilot_system_prompt,
        metadata={
            "context_type": "report",
            "context_id": str(report.id),
            "refresh_token": report.refresh_token,
        },
        user_id=user_id,
    )

    logger.info(
        "[Reports] co-pilot 对话已创建: report_id=%s, conv_id=%s, charts=%d, spec_extracted=%s",
        report_id, conv.id, len(charts_list), _spec_extracted,
    )
    return {
        "success": True,
        "data": {
            "conversation_id": str(conv.id),
            "created": True,
            "spec_extracted": _spec_extracted,
        },
    }


@router.get("/{report_id}/spec-meta")
async def get_report_spec_meta(
    report_id: str,
    token: str = Query(..., description="Report.refresh_token（无需 JWT）"),
    db: Session = Depends(get_db),
):
    """
    通过 refresh_token 获取报告的 spec 元数据（无需 JWT）。

    适用场景：
    - /report-view 分屏页需要在 AI Pilot 侧边面板中注入报表 spec，
      使 AI 能理解报表结构并调用 PUT /spec 接口进行修改。
    """
    try:
        uid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(400, "无效的报告 ID")

    report = db.query(Report).filter(Report.id == uid).first()
    if not report:
        raise HTTPException(404, "报告不存在")

    if not secrets.compare_digest(report.refresh_token or "", token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无效的刷新令牌")

    # D1: 懒更新 — charts 为 NULL 或空列表时尝试从 HTML 文件提取 spec 并回填 DB
    if not report.charts and report.report_file_path:
        try:
            from backend.services.report_service import extract_spec_from_html_file
            from sqlalchemy.orm.attributes import flag_modified
            _abs = (_CUSTOMER_DATA_ROOT / report.report_file_path).resolve()
            _spec = extract_spec_from_html_file(_abs)
            if _spec and _spec.get("charts"):
                report.charts = _spec.get("charts", [])
                report.filters = _spec.get("filters", []) or (report.filters or [])
                report.theme = _spec.get("theme", report.theme or "light")
                flag_modified(report, "charts")
                flag_modified(report, "filters")
                try:
                    db.commit()
                    db.refresh(report)
                    logger.info(
                        "[Reports/spec-meta] 懒更新 spec 成功: report_id=%s, charts=%d",
                        report_id, len(report.charts or []),
                    )
                except Exception as _ce:
                    db.rollback()
                    logger.warning("[Reports/spec-meta] 懒更新提交失败（非致命）: %s", _ce)
        except Exception as _e:
            logger.warning("[Reports/spec-meta] 从 HTML 提取 spec 失败（非致命）: %s", _e)

    # D2: html_context 兜底字段 — 提取仍失败时提供 HTML 文本摘要
    d = _report_to_dict(report)
    if not report.charts and report.report_file_path:
        try:
            from backend.services.report_service import extract_html_context
            _abs = (_CUSTOMER_DATA_ROOT / report.report_file_path).resolve()
            _ctx = extract_html_context(_abs, max_chars=1500)
            if _ctx:
                d["html_context"] = _ctx
        except Exception:
            pass

    return {"success": True, "data": d}


@router.post("/{report_id}/rebuild-spec")
async def rebuild_report_spec(
    report_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "create")),
):
    """
    对已固定的报表按需重建 spec（适用于历史存量 / 自由书写 HTML）。

    步骤：
    1. 重新调用提取链（REPORT_SPEC 标记 → ECharts DOM 模式）
    2. 将提取结果写入 Report.charts / .filters / .theme
    3. 若该报表存在绑定的 Pilot 对话且其 system_prompt 含"图表数量：0"，
       同时刷新 system_prompt

    Returns:
        {charts_extracted, charts_count, pilot_prompt_refreshed, source}
    """
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    if not report.report_file_path:
        raise HTTPException(400, "该报表没有关联 HTML 文件，无法重建 spec")

    _abs = (_CUSTOMER_DATA_ROOT / report.report_file_path).resolve()
    if not _abs.exists():
        raise HTTPException(404, f"HTML 文件不存在: {report.report_file_path}")

    # ── 提取 ────────────────────────────────────────────────────────────────
    from backend.services.report_service import extract_spec_from_html_file
    from sqlalchemy.orm.attributes import flag_modified

    _spec = extract_spec_from_html_file(_abs)
    if not _spec or not _spec.get("charts"):
        return {
            "success": False,
            "message": "无法从 HTML 文件中提取图表结构（文件可能没有可识别的图表元素）",
            "charts_extracted": False,
            "charts_count": 0,
        }

    report.charts = _spec.get("charts", [])
    report.filters = _spec.get("filters", []) or (report.filters or [])
    report.theme = _spec.get("theme", report.theme or "light")
    flag_modified(report, "charts")
    flag_modified(report, "filters")
    db.commit()
    db.refresh(report)

    logger.info(
        "[Reports/rebuild-spec] 重建成功: report_id=%s, charts=%d, source=%s",
        report_id, len(report.charts), _spec.get("_source", "report_spec"),
    )

    # ── 刷新绑定的 Pilot 对话 system_prompt ─────────────────────────────────
    _pilot_refreshed = False
    try:
        from backend.models.conversation import Conversation
        from sqlalchemy.orm.attributes import flag_modified as _flag_modified2

        _pilot_conv = (
            db.query(Conversation)
            .filter(
                Conversation.extra_metadata["context_type"].astext == "report",
                Conversation.extra_metadata["context_id"].astext == str(report.id),
            )
            .order_by(Conversation.created_at.desc())
            .first()
        )
        if _pilot_conv:
            _old_prompt = _pilot_conv.system_prompt or ""
            if "图表数量：0" in _old_prompt or "（无图表）" in _old_prompt:
                charts_list = report.charts or []
                _chart_summary = "\n".join(
                    f'  [{i+1}] id="{c.get("id","?")}" title="{c.get("title","?")}" type="{c.get("chart_type","?")}"'
                    for i, c in enumerate(charts_list)
                )
                _new_prompt = (
                    f"[Co-pilot 模式] 当前报表：{report.name}\n"
                    f"report_id：{report.id}\n"
                    f"refresh_token：{report.refresh_token}\n"
                    f"图表数量：{len(charts_list)}\n"
                    f"图表列表：\n{_chart_summary or '  （无图表）'}\n"
                    f"图表配置（详细）：{json.dumps(charts_list, ensure_ascii=False)[:4000]}\n"
                    f"过滤器：{json.dumps(report.filters or [], ensure_ascii=False)[:500]}\n"
                    f"主题：{report.theme}\n\n"
                    "## 修改报表的 MCP 工具（直接调用，无需 JWT）\n\n"
                    "### 工具 1：report__get_spec\n"
                    "读取报表当前完整 spec，修改前先调用确认所有图表。\n"
                    "参数：report_id（见上方）、token（即 refresh_token）\n\n"
                    "### 工具 2：report__update_single_chart（优先使用）\n"
                    "局部更新单个图表，merge 操作，不影响其他图表。\n"
                    "参数：report_id、token、chart_id、chart_patch（只含需修改字段）\n\n"
                    "### 工具 3：report__update_spec（全量更新）\n"
                    "更新整个报表 spec，适用于添加/删除图表或批量修改。\n"
                    "⚠️ spec.charts 必须包含所有图表，缺少的图表将永久删除！\n"
                    "参数：report_id、token、spec（完整 spec JSON 含所有图表）\n\n"
                    "请基于以上报表信息协助用户修改报表。修改完成后告知用户「报表已更新，请查看预览」。"
                )
                _meta = dict(_pilot_conv.extra_metadata or {})
                _meta["system_prompt"] = _new_prompt
                _pilot_conv.extra_metadata = _meta
                _flag_modified2(_pilot_conv, "extra_metadata")
                db.commit()
                _pilot_refreshed = True
                logger.info(
                    "[Reports/rebuild-spec] 刷新 Pilot system_prompt: conv_id=%s", _pilot_conv.id
                )
    except Exception as _pe:
        logger.warning("[Reports/rebuild-spec] 刷新 Pilot prompt 失败（非致命）: %s", _pe)

    return {
        "success": True,
        "charts_extracted": True,
        "charts_count": len(report.charts),
        "pilot_prompt_refreshed": _pilot_refreshed,
        "source": _spec.get("_source", "report_spec"),
        "data": _report_to_dict(report),
    }


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))
    return {"success": True, "data": _report_to_dict(report)}


@router.delete("/{report_id}")
async def delete_report(
    report_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "delete")),
):
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    # 删除本地 HTML 文件
    if report.report_file_path:
        html_path = _CUSTOMER_DATA_ROOT / report.report_file_path
        if html_path.exists():
            html_path.unlink(missing_ok=True)
            logger.info("[Reports] 删除 HTML 文件: %s", html_path)

    db.delete(report)
    db.commit()
    return {"success": True, "message": "报告已删除"}


@router.get("/{report_id}/summary-status")
async def get_summary_status(
    report_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))
    return {
        "success": True,
        "data": {
            "status": report.summary_status,
            "llm_summary": report.llm_summary if report.summary_status == "done" else None,
        },
    }


def _report_to_dict(r: Report) -> Dict:
    d = r.to_dict()
    # 补充预览/下载 URL（前端通过 refresh_token 访问，无需 JWT）
    if r.report_file_path and r.refresh_token:
        d["html_url"] = f"/api/v1/reports/{r.id}/html?token={r.refresh_token}"
        d["download_url"] = f"/api/v1/reports/{r.id}/html?token={r.refresh_token}&download=true"
    elif r.report_file_path:
        # 兜底：无 refresh_token 的旧记录仍提供文件路径下载
        d["download_url"] = f"/api/v1/files/download?path={r.report_file_path}"
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 4. PDF / PPTX 导出
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{report_id}/export")
async def export_report(
    report_id: str,
    req: ExportReportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    异步导出 PDF 或 PPTX。返回 job_id，前端轮询 /export-status。
    """
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    if req.format not in ("pdf", "pptx"):
        raise HTTPException(400, "format 必须是 pdf 或 pptx")

    if not report.report_file_path:
        raise HTTPException(400, "报告尚未生成 HTML 文件，无法导出")

    job_id = str(uuid.uuid4())
    _export_jobs[job_id] = {
        "status": "pending",
        "report_id": report_id,
        "format": req.format,
        "created_at": datetime.utcnow().isoformat(),
        "output_path": None,
        "error": None,
    }

    html_path = str(_CUSTOMER_DATA_ROOT / report.report_file_path)
    output_dir = str(_CUSTOMER_DATA_ROOT / username / "exports")
    title = report.name or "report"

    background_tasks.add_task(
        _run_export_job, job_id, req.format, html_path, output_dir, title, report.llm_summary or ""
    )

    return {"success": True, "data": {"job_id": job_id}}


@router.get("/{report_id}/export-status")
async def get_export_status(
    report_id: str,
    job_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    username = getattr(current_user, "username", "default")
    # 验证报告归属（防止跨用户查询）
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    job = _export_jobs.get(job_id)
    if not job or job["report_id"] != report_id:
        raise HTTPException(404, "导出任务不存在")

    resp: Dict[str, Any] = {"success": True, "data": job}
    if job["status"] == "done" and job["output_path"]:
        # 返回下载 URL（通过 files API）
        try:
            rel = str(Path(job["output_path"]).relative_to(_CUSTOMER_DATA_ROOT))
        except ValueError:
            rel = job["output_path"]
        resp["data"]["download_url"] = f"/api/v1/files/download?path={rel}"
    return resp


async def _run_export_job(
    job_id: str,
    fmt: str,
    html_path: str,
    output_dir: str,
    title: str,
    llm_summary: str,
) -> None:
    _export_jobs[job_id]["status"] = "running"
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        title_slug = _slugify(title)[:40]

        if fmt == "pdf":
            from backend.services.pdf_export_service import html_to_pdf
            out = os.path.join(output_dir, f"{title_slug}_{ts}.pdf")
            await html_to_pdf(html_path, out)
        else:
            from backend.services.pptx_export_service import html_to_pptx
            out = os.path.join(output_dir, f"{title_slug}_{ts}.pptx")
            await html_to_pptx(html_path, out, title=title, summary=llm_summary)

        _export_jobs[job_id]["status"] = "done"
        _export_jobs[job_id]["output_path"] = out
        logger.info("[Reports] 导出完成: job=%s, file=%s", job_id, out)
    except Exception as e:
        _export_jobs[job_id]["status"] = "failed"
        _export_jobs[job_id]["error"] = str(e)
        logger.error("[Reports] 导出失败: job=%s, error=%s", job_id, e, exc_info=True)
