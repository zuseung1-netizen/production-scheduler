"""Help tab — 배정 알고리즘 설명 / Config 설정 / 마스터 필드 / 간트 UI 가이드."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTextBrowser, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from data.repositories import ConfigRepo


# ─── Config 키 정의 ────────────────────────────────────────────────────────────
# (key, 설명, 기본값, 타입힌트)
_CONFIG_DEFS = [
    ("crp_excel_path",
     "CRP Excel 파일 경로. 'App Config > CRP Path'에서 설정하거나 여기서 직접 입력.",
     "", "텍스트"),
    ("max_pull_days",
     "자동 배정 시 납기 기준 최대 몇 일 이전 슬롯까지 사용할지 제한. 값이 클수록 더 앞당겨 배정 가능.",
     "45", "정수"),
    ("plan_horizon_weeks",
     "간트 차트 기본 표시 기간 (주 단위). 뷰바의 호라이즌 버튼과 동기화.",
     "4", "정수"),
    ("room_assign_mode",
     "생산실 배정 방식. CAPACITY=잔여 캐파 많은 방 우선, UPH=생산속도 높은 방 우선.",
     "CAPACITY", "CAPACITY 또는 UPH"),
    ("material_due_merge_days",
     "반제품 수요 묶음 기준 일수. 납기 차이가 이 일수 이내인 수요를 하나의 Material 계획으로 통합.",
     "21", "정수"),
    ("max_consolidation_days",
     "캠페인 콘솔리데이션 허용 납기 차이 (일). 같은 SKU 수주 중 납기 차이가 이 값 이내인 수주를 "
     "하나의 캠페인 배치로 묶어 연속 생산. SKU별 campaign_mode=0이면 무시.",
     "7", "정수"),
    ("continuity_window_days",
     "⚠ 미구현 — 연속성 우선 적용 납기 임계일. 납기까지 이 일수 이내라면 연속성 보너스가 납기 역방향보다 우선.",
     "3", "정수"),
    ("max_sku_per_room_day",
     "⚠ 미구현 — 하루 동안 한 생산실에서 허용하는 최대 SKU 종수. Cap 도달 시 신규 SKU 배정 차단. 0=무제한.",
     "2", "정수 (0=무제한)"),
]


# ─── HTML 공통 스타일 ──────────────────────────────────────────────────────────
_CSS = """
<style>
body  { font-family:'Segoe UI',Arial,sans-serif; font-size:13px;
        color:#16213d; line-height:1.7; margin:16px 24px; }
h2    { font-size:15px; font-weight:700; color:#1a3a6e;
        margin:20px 0 8px; border-bottom:2px solid #dde5f0; padding-bottom:5px; }
h3    { font-size:13px; font-weight:700; color:#2f5fd6; margin:14px 0 6px; }
p     { margin:4px 0 10px; }
ul,ol { margin:4px 0 10px 22px; }
li    { margin-bottom:5px; }
table { border-collapse:collapse; width:100%; margin:8px 0 16px; }
th    { background:#f0f4ff; color:#1a3a6e; font-weight:700;
        padding:7px 10px; text-align:left;
        border:1px solid #d0d8ef; font-size:12px; }
td    { padding:6px 10px; border:1px solid #e2e8f0;
        font-size:12px; vertical-align:top; }
tr:nth-child(even) td { background:#f8faff; }
code  { background:#f0f2f7; border-radius:3px; padding:1px 5px;
        font-family:Consolas,monospace; font-size:11.5px; color:#2451c2; }
.note { background:#f0f5ff; border-left:3px solid #4f8df0;
        padding:9px 14px; border-radius:0 5px 5px 0;
        margin:10px 0; font-size:12px; }
.warn { background:#fef3e0; border-left:3px solid #e09a1f;
        padding:9px 14px; border-radius:0 5px 5px 0;
        margin:10px 0; font-size:12px; }
.tag  { display:inline-block; padding:1px 7px; border-radius:8px;
        font-size:11px; font-weight:700; }
.t-blue  { background:#dde9ff; color:#2451c2; }
.t-green { background:#e6f4ea; color:#1d8a4a; }
.t-amber { background:#fef3e0; color:#b9760a; }
.t-red   { background:#fbe7e7; color:#c2342f; }
.t-gray  { background:#eee;    color:#555; }
</style>
"""

def _html(body: str) -> str:
    return f"<html><head>{_CSS}</head><body>{body}</body></html>"


# ─── 배정 알고리즘 ─────────────────────────────────────────────────────────────
_ALGO_HTML = _html("""
<h2>자동 배정 알고리즘</h2>

<h3>1단계 — SO 정렬</h3>
<p>OPEN 상태의 SO를 다음 순서로 정렬하여 순서대로 배정합니다.</p>
<ul>
  <li>Priority 낮은 숫자 우선 (미설정 SO는 후순위)</li>
  <li>Priority가 같으면 Received At 빠른 순 (FIFO)</li>
</ul>

<h3>2단계 — 슬롯 맵 생성</h3>
<p>계획 가능한 <strong>(날짜 × 생산실 × 공정 × Shift)</strong> 조합과 잔여 캐파를 사전 계산합니다.</p>
<ul>
  <li>CRP Excel의 HC 기반으로 <code>UPH × Shift시간 = Shift 캐파</code> 산출</li>
  <li>Calendar CLOSED / HOLD 슬롯 제외</li>
  <li>잠금(Locked) 계획은 캐파를 미리 차감하고 해당 생산실-Shift를 선점</li>
</ul>

<h3>3단계 — 역방향 배정 (Backward Scheduling)</h3>
<p>각 SO의 공정 라우팅을 <strong>최종 공정 → 전공정</strong> 순으로 역방향 배정합니다.</p>
<ul>
  <li>최종 공정 배정 마감 = 납기일 − <code>SKU.post_lead_days</code></li>
  <li>전공정 상한 = 후공정 첫 슬롯에서 <code>min_gap_shifts</code>만큼 앞</li>
</ul>

<h3>4단계 — 슬롯 선택 우선순위</h3>
<table>
  <tr><th>순위</th><th>기준</th><th>설명</th></tr>
  <tr><td>1</td><td>납기 역방향</td><td>납기에 가까운 날부터 역방향으로 채움</td></tr>
  <tr><td>2</td><td>연속성 보너스</td><td>같은 날 같은 생산실에 동일 SKU+공정 → +2 보너스,
      동일 공정만 → +1 보너스</td></tr>
  <tr><td>3</td><td>잔여 캐파</td><td>캐파가 많은 생산실 우선 (CAPACITY 모드 기준)</td></tr>
</table>
<div class="note">
  <strong>continuity_window_days</strong> 이내의 납기라면 연속성 보너스가 납기보다 우선 적용됩니다.<br>
  예: 값이 3이고 납기까지 2일 남았다면, 같은 방에서 연속 생산 슬롯을 더 이른 날에 먼저 배정합니다.
</div>

<h3>5단계 — 라인 클리어런스 (1 공정 / room-shift)</h3>
<p>한 생산실의 한 Shift에는 <strong>하나의 공정만</strong> 배정됩니다.</p>
<ul>
  <li>어떤 공정이 배정되는 순간, 같은 생산실+Shift의 다른 공정 슬롯은 즉시 차단</li>
  <li>기존 계획 위반 시 간트 카드에 <span style="color:#e0413a;">●</span> 충돌 점 표시</li>
</ul>

<h3>6단계 — SKU 전환 Cap</h3>
<p><code>max_sku_per_room_day</code> 값으로 하루에 한 생산실에서 허용하는 SKU 종수를 제한합니다.</p>
<ul>
  <li>Cap <strong>미만</strong>: 소프트 — 기존 SKU에 연속성 보너스, 신규 SKU도 후순위로 허용</li>
  <li>Cap <strong>도달</strong>: 하드 — 신규 SKU는 해당 생산실+날짜 사용 불가</li>
  <li>0으로 설정 시 제한 없음 (기존 방식)</li>
</ul>
<div class="warn">
  Cap을 너무 낮게 설정하면 납기 급한 SO가 다른 방으로 밀릴 수 있습니다.<br>
  2-shift 운영 시 기본값 <strong>2</strong>를 권장합니다.
</div>

<h3>7단계 — Material 자동 배정</h3>
<p>SKU 배정 후, <code>requires_material_code</code>가 설정된 공정에서 반제품 수요를 수집합니다.</p>
<ul>
  <li>납기 차이 <code>material_due_merge_days</code>일 이내의 수요를 하나의 그룹으로 묶음</li>
  <li>Material 고유 공정 라우팅을 역방향으로 배정</li>
</ul>
""")


# ─── 마스터 필드 설명 ──────────────────────────────────────────────────────────
_MASTERS_HTML = _html("""
<h2>마스터 데이터 필드 설명</h2>

<h3>SKU Master</h3>
<table>
  <tr><th>필드</th><th>설명</th><th>비고</th></tr>
  <tr><td><code>sku_code</code></td><td>완제품 고유 코드 (PK). 영문+숫자 권장</td><td>Process Routing, SO와 연결</td></tr>
  <tr><td><code>uom</code></td><td>내부 단위 환산값. SKU 1개 = UoM개 내부단위</td><td>캐파 계산에 사용</td></tr>
  <tr><td><code>post_lead_days</code></td><td>최종 공정 완료 후 출하까지 필요한 일수</td><td>납기 역방향 기준점 = 납기 − post_lead_days</td></tr>
</table>

<h3>Material Master</h3>
<table>
  <tr><th>필드</th><th>설명</th><th>비고</th></tr>
  <tr><td><code>material_code</code></td><td>반제품 고유 코드 (PK)</td><td>Process Routing의 requires_material_code와 연결</td></tr>
  <tr><td><code>uom</code></td><td>내부 단위 환산값</td><td>SKU와 동일 개념</td></tr>
  <tr><td><code>post_lead_days</code></td><td>반제품 완성 후 다음 SKU 공정 투입 전 대기 일수</td><td></td></tr>
</table>

<h3>Process Routing</h3>
<table>
  <tr><th>필드</th><th>설명</th><th>비고</th></tr>
  <tr><td><code>entity_type</code></td><td>SKU 또는 MATERIAL</td><td></td></tr>
  <tr><td><code>entity_code</code></td><td>해당 SKU 또는 Material 코드</td><td></td></tr>
  <tr><td><code>process_seq</code></td><td>공정 순서 번호 (1부터 오름차순)</td><td></td></tr>
  <tr><td><code>process_name</code></td><td>공정명. Room Master의 공정명과 정확히 일치해야 함</td><td></td></tr>
  <tr><td><code>is_final_seq</code></td><td>최종 공정 여부. 간트에서 FINAL 뱃지 표시</td><td>MRP 기준점, 1개만 설정</td></tr>
  <tr><td><code>allowed_room_types</code></td><td>이 공정에서 사용 가능한 생산실 유형 (콤마 구분)</td><td>Room Master의 room_type과 매핑</td></tr>
  <tr><td><code>requires_material_code</code></td><td>이 공정 전에 필요한 반제품 코드</td><td>Material 수요 자동 생성</td></tr>
  <tr><td><code>min_gap_shifts</code></td><td>직전 공정 종료 후 이 공정 시작 전 필요한 빈 Shift 수</td><td>0=인접 OK, 1=1Shift 공백, 2≈1일(2shift 운영 기준)</td></tr>
</table>

<h3>Room / Process Master</h3>
<table>
  <tr><th>필드</th><th>설명</th><th>비고</th></tr>
  <tr><td><code>room_code</code></td><td>생산실 고유 코드</td><td></td></tr>
  <tr><td><code>room_type</code></td><td>생산실 유형. Process Routing의 allowed_room_types와 매핑</td><td></td></tr>
  <tr><td><code>process_name</code></td><td>이 생산실에서 수행 가능한 공정명</td><td></td></tr>
  <tr><td><code>process_type</code></td><td>AUTO(고정 UPH) 또는 MANUAL(UPPH × HC)</td><td></td></tr>
  <tr><td><code>upph</code></td><td>MANUAL 전용: 인원 1명당 시간당 생산량</td><td>AUTO는 미사용</td></tr>
  <tr><td><code>uph_fixed</code></td><td>AUTO 전용: 고정 시간당 생산량</td><td>MANUAL은 미사용</td></tr>
  <tr><td><code>hc_min / hc_max</code></td><td>MANUAL: 투입 가능 최소/최대 인원</td><td></td></tr>
  <tr><td><code>hc_fixed</code></td><td>AUTO: 슬롯 활성화에 필요한 최소 인원. CRP에 이 인원 이상 있어야 배정 가능</td><td></td></tr>
</table>

<h3>Shift Config</h3>
<table>
  <tr><th>필드</th><th>설명</th><th>비고</th></tr>
  <tr><td><code>shift_no</code></td><td>Shift 번호 (1=Day, 2=Night, 3=Third)</td><td></td></tr>
  <tr><td><code>start_time / end_time</code></td><td>Shift 시작·종료 시각 (HH:MM)</td><td>자정 넘어가는 Night Shift 자동 처리</td></tr>
</table>

<h3>Calendar</h3>
<table>
  <tr><th>상태</th><th>표시</th><th>설명</th></tr>
  <tr><td><span class="tag t-green">OPEN</span></td><td>흰 배경</td><td>가동 가능. 자동 배정 대상 슬롯</td></tr>
  <tr><td><span class="tag t-gray">CLOSED</span></td><td>회색 해칭</td><td>가동 불가. 슬롯 맵에서 완전 제외</td></tr>
  <tr><td><span class="tag t-amber">HOLD</span></td><td>주황 해칭</td><td>일시 보류. 간트 시각 표시만, 배정 제외</td></tr>
</table>
<p>일괄 설정: <strong>Open Weekdays / Close Weekends / Open All / Close All</strong> 버튼 사용.</p>

<h3>Config</h3>
<p>상단 <strong>Config 설정</strong> 탭에서 각 키의 설명과 현재값을 확인하고 직접 편집할 수 있습니다.</p>

<h2>Excel 업로드 동작 방식</h2>
<p>각 데이터 유형을 Excel로 업로드할 때 기존 데이터가 어떻게 처리되는지 정리합니다.</p>
<table>
  <tr><th>업로드 대상</th><th>업로드 메뉴</th><th>동작 방식</th><th>주의사항</th></tr>
  <tr>
    <td><strong>Sales Order (SO)</strong></td>
    <td>Sales Orders &gt; Upload SO</td>
    <td>
      <span class="tag t-blue">Upsert</span><br>
      PK(so_number + sku_code + line_item) 기준으로 신규 추가 또는 기존 갱신.<br>
      <strong>파일에 없는 OPEN/HOLD SO는 자동으로 CLOSED 처리됩니다.</strong>
    </td>
    <td>업로드 전 미리보기(diff)에서 CLOSED 전환 항목을 반드시 확인하세요.</td>
  </tr>
  <tr>
    <td><strong>SKU Master</strong></td>
    <td>Masters &gt; SKU Master &gt; 📥 Upload</td>
    <td>
      <span class="tag t-blue">Upsert</span><br>
      sku_code 기준 신규 추가 또는 기존 갱신.<br>
      파일에 없는 기존 SKU는 <strong>삭제되지 않고 유지</strong>됩니다.
    </td>
    <td>SKU를 삭제하려면 수동으로 제거해야 합니다.</td>
  </tr>
  <tr>
    <td><strong>Material Master</strong></td>
    <td>Masters &gt; Material Master &gt; 📥 Upload</td>
    <td>
      <span class="tag t-blue">Upsert</span><br>
      material_code 기준 신규 추가 또는 기존 갱신.<br>
      파일에 없는 기존 Material은 <strong>삭제되지 않고 유지</strong>됩니다.
    </td>
    <td>SKU와 동일 정책.</td>
  </tr>
  <tr>
    <td><strong>Room / Process Master</strong></td>
    <td>Masters &gt; Room/Process &gt; 📥 Upload</td>
    <td>
      <span class="tag t-blue">Upsert</span><br>
      (room_code + process_name) 기준 신규 추가 또는 기존 갱신.<br>
      파일에 없는 기존 Room은 <strong>삭제되지 않고 유지</strong>됩니다.
    </td>
    <td>생산실을 폐기할 경우 수동 삭제 필요.</td>
  </tr>
  <tr>
    <td><strong>Process Routing</strong></td>
    <td>Masters &gt; Process Routing &gt; 📥 Upload</td>
    <td>
      <span class="tag t-red">Entity 단위 전체 교체</span><br>
      파일에 포함된 SKU/Material 코드의 기존 라우팅을 <strong>전량 삭제 후 재삽입</strong>합니다.<br>
      파일에 포함되지 않은 다른 SKU/Material의 라우팅은 유지됩니다.
    </td>
    <td>공정 순서 변경이나 일부 step 삭제 시 해당 Entity의 모든 step을 파일에 포함하여 업로드해야 합니다.</td>
  </tr>
  <tr>
    <td><strong>Inventory</strong></td>
    <td>📦 Inventory &gt; Upload Inventory</td>
    <td>
      <span class="tag t-blue">Upsert</span><br>
      (sku_code + lot_number) 기준 신규 추가 또는 기존 갱신.<br>
      파일에 없는 기존 LOT는 <strong>삭제되지 않고 유지</strong>됩니다.
    </td>
    <td>LOT를 삭제하려면 Inventory 탭에서 수동으로 제거하거나 status를 CONSUMED/EXPIRED로 변경하세요.</td>
  </tr>
</table>

<div class="note">
  <strong>권장 업로드 순서:</strong>
  Room/Process → Material → SKU → Process Routing → SO → CRP (경로 설정 후 Refresh)
</div>
<div class="warn">
  <strong>Process Routing 업로드 시</strong> 해당 Entity(SKU/Material)의 기존 라우팅이 모두 삭제되므로,
  일부 공정만 수정하더라도 전체 라우팅을 파일에 포함시켜 업로드하세요.
</div>
""")


# ─── 간트 UI 가이드 ────────────────────────────────────────────────────────────
_GANTT_HTML = _html("""
<h2>간트 차트 UI 사용법</h2>

<h3>화면 구성</h3>
<table>
  <tr><th>영역</th><th>설명</th></tr>
  <tr><td>Top Bar</td><td>페이지 제목·검색창·KPI 필·주요 액션 버튼 (Execute Plan / Pull Forward / Clear Plan)</td></tr>
  <tr><td>View Bar</td><td>Y축 모드 프리셋·디멘션 선택기·Shift 토글·호라이즌·날짜 선택기·우측 기능 버튼</td></tr>
  <tr><td>고정 헤더</td><td>날짜 행 + Cap 활용율 바. 가로 스크롤 시 고정 유지</td></tr>
  <tr><td>간트 본문</td><td>생산실/공정/SKU별 행 × 날짜/Shift별 열 그리드</td></tr>
  <tr><td>Unplanned 패널</td><td>우측 슬라이드 패널. 미배정 SO·공정 목록 표시</td></tr>
</table>

<h3>카드 색상 및 아이콘</h3>
<table>
  <tr><th>요소</th><th>의미</th></tr>
  <tr><td>카드 왼쪽 색상 바 (4px)</td><td>SKU별 고유 색상. Material 계획=보라, 납기 지연 계획=빨강</td></tr>
  <tr><td><span style="color:#e0413a;font-size:14px;">●</span> 충돌 점 (좌상단)</td><td>캐파 초과 또는 동일 Shift 내 다중 공정 배정 위반</td></tr>
  <tr><td>🔒 잠금 아이콘 (우상단)</td><td>수동 잠금된 계획. 자동 배정 시 삭제/이동 안 됨. 점선 테두리로도 표시</td></tr>
  <tr><td><span style="background:#e09a1f;color:#fff;padding:1px 6px;border-radius:3px;font-size:11px;">FINAL</span> 뱃지</td><td>최종 공정 (is_final_seq=1). MRP 기준점</td></tr>
  <tr><td>금색 테두리</td><td>콘솔리데이션 그룹. 같은 UUID를 공유하는 계획</td></tr>
  <tr><td>납기 태그 (예: 6.24)</td><td>납기까지 7일 이내 계획에 표시. 지연 시 빨간 배경</td></tr>
  <tr><td>파란 세로선</td><td>오늘 날짜 기준선</td></tr>
  <tr><td>빨간 점선 세로선</td><td>SO의 납기일 기준선. 고객명 또는 SO번호 레이블 표시</td></tr>
  <tr><td>Cap 바 (헤더 하단)</td><td>날짜별 캐파 활용율. 녹색(&lt;60%) → 주황(60~90%) → 빨강(&gt;90%)</td></tr>
  <tr><td>회색 해칭</td><td>Calendar CLOSED 또는 주말 슬롯</td></tr>
  <tr><td>주황 해칭</td><td>Calendar HOLD 슬롯</td></tr>
</table>

<h3>Y축 모드</h3>
<table>
  <tr><th>모드</th><th>설명</th><th>언제 사용</th></tr>
  <tr><td><strong>Room</strong></td><td>생산실 단위 행. 서브라벨에 공정명 표시</td><td>전체 부하 현황 파악</td></tr>
  <tr><td><strong>Room › Proc</strong></td><td>생산실 × 공정 조합으로 행 분리. 왼쪽=생산실, 오른쪽=공정</td><td>다중 공정 생산실 상세 분석</td></tr>
  <tr><td><strong>SKU</strong></td><td>SKU 단위 행</td><td>SKU별 생산 일정 확인</td></tr>
</table>

<h3>드래그 이동</h3>
<ul>
  <li>계획 카드를 드래그하여 날짜·Shift·생산실 변경 가능 (Room 모드에서만 생산실 변경)</li>
  <li>이동 시 사유 입력 필수 (변경 이력 저장)</li>
  <li><strong>Ctrl + 드래그</strong>: 스플릿 모드 — 드롭 위치에 일부 수량을 분리 배정</li>
  <li>타겟 생산실이 해당 공정을 미지원하면 <span style="color:#e0413a;">✕ Not supported</span> 빨간 고스트 표시 후 드롭 차단</li>
</ul>

<h3>검색 필터</h3>
<p>Top Bar 검색창에 SO번호·SKU코드·고객명을 입력하면 해당 계획만 강조, 나머지는 15% 투명도로 흐리게 표시됩니다.</p>

<h3>체크박스 선택 → 콘솔리데이션</h3>
<ol>
  <li>카드 좌상단 체크박스 클릭으로 개별 선택 (클릭 히트 영역: 20×20px)</li>
  <li>2개 이상 선택 시 View Bar의 <strong>🔗 Consolidate</strong> 버튼 활성화</li>
  <li>조건: 같은 SKU + 같은 생산실 + 같은 공정이어야 콘솔리데이션 가능</li>
  <li>같은 Shift → 수량 합산 병합 / 다른 Shift → 연속 Shift로 재배치</li>
  <li>콘솔리데이션 완료 시 그룹 UUID 부여, 자동 잠금 처리, 금색 테두리 표시</li>
</ol>

<h3>컨텍스트 메뉴 (계획 카드 우클릭)</h3>
<table>
  <tr><th>항목</th><th>기능</th></tr>
  <tr><td>🔒 Lock / 🔓 Unlock</td><td>계획 잠금/해제. 잠긴 계획은 자동 재배정 시 보존</td></tr>
  <tr><td>✂ Split</td><td>계획 수량을 복수로 분할하여 여러 슬롯에 배분</td></tr>
  <tr><td>Pull Out</td><td>해당 계획을 오늘 날짜로 당겨옴</td></tr>
  <tr><td>🔓 Break Consolidation</td><td>콘솔리데이션 그룹 해제 및 잠금 해제</td></tr>
  <tr><td>📝 Add Memo</td><td>계획에 메모 추가. 카드 툴팁에 표시</td></tr>
  <tr><td>🗑 Delete</td><td>계획 삭제 (사유 입력 필수, 이력 저장)</td></tr>
  <tr><td>⛔ Hard Block</td><td>해당 슬롯을 Calendar CLOSED로 차단</td></tr>
  <tr><td>➕ Add Plan</td><td>빈 셀 우클릭 시 수동 계획 추가 (Room 모드 전용)</td></tr>
</table>

<h3>호라이즌 및 날짜 이동</h3>
<ul>
  <li><strong>2W / 4W / 6W / 3M</strong> 버튼으로 표시 기간 전환</li>
  <li>날짜 선택기로 시작일 변경</li>
  <li><strong>Shift 토글</strong>: 날짜 단위 열 ↔ Shift 단위 열 전환</li>
</ul>

<h3>KPI 필 (Top Bar)</h3>
<table>
  <tr><th>필</th><th>기준</th></tr>
  <tr><td><span class="tag t-green">● On time</span></td><td>납기까지 4일 이상 여유</td></tr>
  <tr><td><span class="tag t-amber">● At risk</span></td><td>납기까지 0~3일 이내</td></tr>
  <tr><td><span class="tag t-red">● Late</span></td><td>납기 초과</td></tr>
</table>
""")


# ─── HelpTab 위젯 ──────────────────────────────────────────────────────────────

class HelpTab(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            "QTabWidget::pane { border:none; background:#fff; }"
            "QTabBar::tab { padding:8px 20px; font-size:12px; font-weight:600;"
            " color:#6b7280; border:none; border-bottom:2px solid transparent;"
            " background:#fafbfc; }"
            "QTabBar::tab:selected { color:#2f5fd6; border-bottom-color:#2f5fd6; background:#fff; }"
            "QTabBar::tab:hover:!selected { color:#3a4255; background:#f0f2f7; }"
        )

        self._tabs.addTab(self._make_browser(_ALGO_HTML),    "배정 알고리즘")
        self._config_widget = self._make_config_tab()
        self._tabs.addTab(self._config_widget,               "Config 설정")
        self._tabs.addTab(self._make_browser(_MASTERS_HTML), "마스터 필드")
        self._tabs.addTab(self._make_browser(_GANTT_HTML),   "간트 UI")

        layout.addWidget(self._tabs)

    # ── 브라우저 탭 ────────────────────────────────────────────────────────────

    @staticmethod
    def _make_browser(html: str) -> QTextBrowser:
        w = QTextBrowser()
        w.setOpenExternalLinks(False)
        w.setHtml(html)
        w.setStyleSheet(
            "QTextBrowser { background:#fff; border:none; padding:6px 4px; }")
        return w

    # ── Config 탭 ─────────────────────────────────────────────────────────────

    def _make_config_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:#fff;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # ── 헤더 ─────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("App Config")
        title.setStyleSheet(
            "font-size:14px; font-weight:700; color:#16213d; background:transparent;")
        hdr.addWidget(title)

        sub = QLabel("Edit the Current Value column, then click Save.")
        sub.setStyleSheet("font-size:11px; color:#9aa1b3; background:transparent;")
        hdr.addWidget(sub)
        hdr.addStretch()

        btn_refresh = QPushButton("↻  Refresh")
        btn_refresh.setFixedHeight(30)
        btn_refresh.setStyleSheet(
            "QPushButton { background:#fff; color:#3a4255; border:1px solid #d4d7e0;"
            " border-radius:5px; padding:0 14px; font-size:12px; font-weight:600; }"
            "QPushButton:hover { background:#f5f6fa; }"
        )
        btn_refresh.clicked.connect(self._load_config)
        hdr.addWidget(btn_refresh)

        self._btn_save = QPushButton("💾  Save")
        self._btn_save.setFixedHeight(30)
        self._btn_save.setStyleSheet(
            "QPushButton { background:#2f5fd6; color:#fff; border:none;"
            " border-radius:5px; padding:0 18px; font-size:12px; font-weight:600; }"
            "QPushButton:hover { background:#2451c2; }"
        )
        self._btn_save.clicked.connect(self._save_config)
        hdr.addWidget(self._btn_save)
        layout.addLayout(hdr)

        # ── 테이블 ───────────────────────────────────────────────────────────
        self._tbl = QTableWidget(len(_CONFIG_DEFS), 4)
        self._tbl.setHorizontalHeaderLabels(["Key", "Description", "Default", "Current Value"])
        hh = self._tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.setStyleSheet(
            "QTableWidget { border:1px solid #e2e4ea; border-radius:6px;"
            " font-size:12px; gridline-color:#edeef3; }"
            "QHeaderView::section { background:#f0f4ff; color:#1a3a6e; font-weight:700;"
            " padding:7px 10px; border:1px solid #d0d8ef; font-size:12px; }"
            "QTableWidget::item { padding:6px 10px; }"
            "QTableWidget::item:selected { background:#dde9ff; color:#16213d; }"
        )
        self._tbl.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)

        for row, (key, desc, default, hint) in enumerate(_CONFIG_DEFS):
            # 키 (read-only, monospace)
            ki = QTableWidgetItem(key)
            ki.setFlags(ki.flags() & ~Qt.ItemFlag.ItemIsEditable)
            ki.setFont(QFont("Consolas", 11))
            ki.setForeground(Qt.GlobalColor.darkBlue)
            self._tbl.setItem(row, 0, ki)

            # 설명 (read-only)
            di = QTableWidgetItem(desc)
            di.setFlags(di.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._tbl.setItem(row, 1, di)

            # 기본값 + 타입힌트 (read-only)
            dv = f"{default}  ({hint})" if hint else default
            dfi = QTableWidgetItem(dv)
            dfi.setFlags(dfi.flags() & ~Qt.ItemFlag.ItemIsEditable)
            dfi.setForeground(Qt.GlobalColor.gray)
            self._tbl.setItem(row, 2, dfi)

            # 현재값 (편집 가능)
            self._tbl.setItem(row, 3, QTableWidgetItem(""))

        self._load_config()
        layout.addWidget(self._tbl, stretch=1)
        return w

    def _load_config(self):
        for row, (key, _, default, _hint) in enumerate(_CONFIG_DEFS):
            val = ConfigRepo.get(key, default)
            self._tbl.item(row, 3).setText(val if val is not None else "")

    def _save_config(self):
        for row, (key, _, default, _hint) in enumerate(_CONFIG_DEFS):
            val = self._tbl.item(row, 3).text().strip()
            ConfigRepo.set(key, val if val else default)
        QMessageBox.information(self, "Saved", "Config saved.\nThe scheduler will apply the new values on the next Execute Plan.")

    def refresh(self):
        self._load_config()
