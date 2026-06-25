# Production Planner — Claude Code 프로젝트 컨텍스트

## 협업 규칙 (최우선 적용)

**사용자가 "구현해줘", "만들어줘", "적용해줘", "해줘" 등 명시적으로 구현을 요청하기 전까지는 절대 코드를 작성하거나 파일을 수정하지 않는다.**

- 기능 아이디어, 문제 제기, 질문, 설명 요청 → 텍스트 답변만 (설계안·트레이드오프·옵션 제시)
- "어떻게 하면 돼?", "이거 되는 거야?" 등의 질문 → 방법 설명만, 코드 없음
- UI/UX 변경 → CLAUDE.md의 UI/UX 설계 검토 절차 별도 적용
- 구현 요청을 받은 경우에만 파일 편집·생성 진행

**기능 구현 완료 시 반드시 git commit 수행한다.**

- 구현 → 구문 검사(`python -m py_compile`) → 앱 실행 확인 → **git commit**
- 한 세션에서 여러 기능 구현 시 레이어 단위(DB / 스케줄러 / UI)로 나눠 커밋
- 스크린샷(*.png), mockup HTML, 내보낸 Excel(GanttPlan_*.xlsx)은 커밋하지 않음 (.gitignore 적용)
- 세션 종료 전 `git status`로 미커밋 파일 없는지 확인

**세션 내 중요 결정/구현 완료 시 메모리를 즉시 저장한다.**

- 새 기능 구현 완료 → `project_implemented_features.md` 업데이트
- 보류 결정 → `project_deferred_features.md` 추가
- 설계 함정 발견 → `project_architecture_decisions.md` 추가

**앱 테스팅 프로토콜 (GUI 자동화 시 반드시 준수)**

앱 실행 후 GUI 동작을 직접 확인하거나 자동화할 때:

1. **모니터 배치**: 터미널(쉘)은 보조 모니터, 앱은 주 모니터. 앱 실행 후 `SetWindowPos`로 주 모니터 좌표(x≈50, y≈50)로 이동
2. **권한 팝업 방지**: 앱 테스팅용 Bash 명령에는 항상 `dangerouslyDisableSandbox: true` 사용 — 권한 팝업 자체가 뜨지 않음
3. **포커스 관리**: 자동화 스크립트(클릭, 드래그 등) 전후 반드시 `SetForegroundWindow(hwnd_app)`로 앱 포커스 명시적 복귀
4. **DPI 주의**: 보조 모니터는 150% DPI → 물리 픽셀 좌표 ÷ 1.5 = 논리 좌표. 앱을 주 모니터로 이동하면 이 문제 회피 가능
5. **스크린샷**: `ImageGrab.grab(all_screens=True)` + 윈도우 rect 기준 크롭

---

## 프로젝트 개요
로컬 실행 Python 생산계획 앱. PyQt6 GUI + SQLite DB + openpyxl(CRP Excel).
추후 PyInstaller로 EXE 배포 예정.

```
python main.py   # 실행
pip install PyQt6 openpyxl   # 의존성
```

---

## 디렉토리 구조

```
production_planner/
├── main.py                   # 진입점 — init_db()를 모든 import 전에 호출
├── planner.db                # SQLite DB (자동 생성)
├── CLAUDE.md                 # 이 파일
│
├── data/
│   ├── database.py           # 스키마 정의 + init_db()
│   ├── repositories.py       # 모든 DB 접근 클래스 (Repository 패턴)
│   └── crp_excel.py          # CRP Excel 읽기/쓰기 (CRPManager 싱글톤)
│
├── core/
│   └── scheduler.py          # 스케줄링 엔진 (Lazy Proxy 패턴)
│
├── ui/
│   ├── main_window.py        # 메인 윈도우 + 7개 탭 구조 + 툴바
│   ├── gantt_tab.py          # 간트 차트 (GanttCanvas + GanttTab)
│   ├── so_tab.py             # SO 관리 탭
│   ├── master_tab.py         # 마스터 관리 탭 (SKU/Material/Routing/Room/Shift/Config)
│   ├── remaining_tabs.py     # CRPTab, ActualsTab, AlertsTab, DashboardTab, LotSampleDialog
│   └── crp_tab.py / actuals_tab.py / alerts_tab.py / dashboard_tab.py  # re-export stubs
│
└── utils/
    └── excel_io.py           # Excel import/export (SO/SKU/Room/Material/ProcessRouting)
```

---

## DB 스키마 (SQLite — planner.db)

| 테이블 | 역할 |
|---|---|
| `sku_master` | 완제품 SKU (sku_code PK, uom, post_lead_days) |
| `material_master` | 반제품 Material (material_code PK, uom, post_lead_days) |
| `process_routing` | SKU+MATERIAL 통합 공정 라우팅 (entity_type+entity_code+process_seq PK, **min_gap_shifts**) |
| `room_master` | 생산실/공정 (room_code+process_name PK, room_type, process_type AUTO/MANUAL, **changeover_shifts**) |
| `shift_config` | Shift 정의 (1=Day 08-20, 2=Night 20-08, 선택적 3=Third) |
| `calendar` | 날짜/Shift/생산실별 가동 여부 (is_open, is_hold) |
| `sales_order` | SO (so_number+sku_code+line_item PK, priority, status OPEN/HOLD/CLOSED) |
| `so_history` | SO 업로드 변경 이력 |
| `so_snapshot` | SO 롤백용 스냅샷 (batch_id 기준) |
| `production_plan` | 생산계획 (entity_type SKU/MATERIAL, is_locked, is_consolidated, consolidation_group) |
| `material_demand_group` | 반제품 수요 그룹 (납기 21일 내 묶음) |
| `plan_history` | 계획 변경 이력 (이동/잠금/삭제 사유 포함) |
| `production_actual` | 생산 실적 (entity_type SKU/MATERIAL, lot_number) |
| `lot_sample` | LOT별 QC 수량 (sample_qty + reject_qty → net_qty = actual - sample - reject) |
| `inventory` | 재고 LOT (sku_code+lot_number UNIQUE, qty_available, expiry_date, status) |
| `so_inventory_allocation` | SO-LineItem ↔ 재고 LOT 배정 (N:N, qty_allocated) |

### process_routing 주요 컬럼
| 컬럼 | 설명 |
|---|---|
| `entity_type` | `'SKU'` 또는 `'MATERIAL'` |
| `entity_code` | sku_code 또는 material_code |
| `process_seq` | 공정 순서 (1부터 오름차순) |
| `is_final_seq` | 1=최종공정(MRP 기준점, `[FINAL]` 메모) |
| `requires_material_code` | 이 공정 전에 필요한 반제품 코드 |
| `min_gap_shifts` | 직전 공정 종료 후 이 공정 시작 전 필요한 **빈 Shift 수** (0=인접 shift OK, 1=1 shift 공백, 2≈1일) |
| `allowed_room_types` | 이 공정에서 쓸 수 있는 생산실 유형 (콤마 구분) |

---

## N+1 쿼리 방지 설계 규칙

**탭 refresh() 또는 루프에서 DB를 호출할 때 반드시 아래 패턴을 따른다.**

### 허용 패턴 (배치 쿼리)
```python
# ❌ N+1 — 절대 금지
for so in sos:
    planned = PlanRepo.planned_qty(so["so_number"], ...)   # SO 수만큼 쿼리

# ✅ 배치 — 루프 전에 전체를 한 번에 가져옴
planned_map = PlanRepo.planned_qty_bulk()   # 1 query
actual_map  = ActualRepo.actual_qty_bulk()  # 1 query
for so in sos:
    key = (so["so_number"], so["sku_code"], so["line_item"])
    planned = planned_map.get(key, 0)
    actual  = actual_map.get(key, 0)
```

### 기존 벌크 메서드 목록
| 메서드 | 반환 타입 | 비고 |
|---|---|---|
| `PlanRepo.planned_qty_bulk()` | `{(so, sku, li): qty}` | 모든 공정 합산 |
| `PlanRepo.last_plan_info_bulk()` | `{(so, sku, li): (date, shift_no)}` | 최후 슬롯 (날짜+shift 최댓값) |
| `ActualRepo.actual_qty_bulk()` | `{(so, sku, li): qty}` | |
| `LotSampleRepo.sample_reject_bulk()` | `{(so, sku, li): (sample, reject)}` | |
| `AllocationRepo.allocation_summary_for_open_sos()` | `{(so, sku, li): allocated}` | 전체 SO 대상 |

### 새 탭/다이얼로그 구현 시 체크리스트
- [ ] SO 목록 루프 안에 `PlanRepo.planned_qty()`, `ActualRepo.actual_qty()` 등 단건 조회 없는지 확인
- [ ] `PlanRepo.for_so()` 를 루프 안에서 쓰면 반드시 `last_plan_info_bulk()`로 대체
- [ ] `SKURepo.get()` 루프 내 호출 → `{code: sku for sku in SKURepo.all()}` 캐시로 대체
- [ ] 벌크 메서드 없는 경우 `repositories.py`에 추가 후 사용

---

## Repository 클래스 목록 (data/repositories.py)

| 클래스 | 주요 메서드 |
|---|---|
| `ConfigRepo` | get / set / all |
| `SKURepo` | all / get / upsert / delete / bulk_upsert |
| `MaterialRepo` | all / get / upsert / delete / bulk_upsert |
| `ProcessRoutingRepo` | for_entity / all / upsert / delete / delete_all_for_entity / validate |
| `RoomRepo` | all / rooms / room_types / processes_for_room / rooms_for_process / upsert |
| `ShiftRepo` | all / upsert |
| `CalendarRepo` | get_open_slots / set_slot / get_slot |
| `SORepo` | all / get / upsert / close / hold / set_priority / history / save_snapshot / rollback |
| `PlanRepo` | all / get / insert / update / delete / lock / for_so / planned_qty / plan_history |
| `MaterialDemandRepo` | insert_group_member / for_group / for_material / delete_group |
| `ActualRepo` | insert / for_plan / for_so / actual_qty / for_entity / recent |
| `LotSampleRepo` | insert / for_actual / total_sample_qty / total_reject_qty / net_qty / update / delete |
| `InventoryRepo` | all / get / available_for_sku / total_available / upsert / delete / update_status / fefo_suggestion |
| `AllocationRepo` | for_so / for_lot / total_allocated / production_needed / allocate / deallocate / confirm_fefo_suggestion / all_allocations |
| `SKUProcessRepo` | legacy alias → ProcessRoutingRepo (하위 호환용) |

---

## 스케줄러 핵심 설계 (core/scheduler.py)

### Lazy Proxy 패턴
```python
# 임포트 시점에 DB를 건드리지 않도록 Lazy 초기화
scheduler = _SchedulerProxy()   # main.py에서 init_db() 이후에 실제 인스턴스 생성
```

### 자동 배정 흐름
1. OPEN SO를 priority(오름차순) → received_at(FIFO) 순 정렬
2. 날짜별/Shift별/생산실별 슬롯 맵 빌드 (CRP Excel HC 기반 캐파 계산)
3. 각 SO-LineItem의 SKU 공정 라우팅(process_routing) 조회
4. **역방향** 배정: 최종 공정부터 납기일 기준으로 backward fill
5. 전공정 배치 상한선 = `_find_pre_cutoff(후공정 첫 슬롯, min_gap_shifts)` — shift 인덱스를 `(1 + min_gap_shifts)` 만큼 뒤로 이동
6. **체인지오버 검사** — 슬롯 배치 전 `_has_changeover_conflict()` 호출. 같은 Room/Process에서 직후 `changeover_shifts`개 슬롯 안에 다른 SKU가 있으면 해당 슬롯 스킵
7. 반제품(MATERIAL) 수요 수집 → 납기 21일 내 묶음 → Material 역방향 배정
8. is_final_seq=1 슬롯에 `[FINAL]` 메모 + MRP 기준점 플래그

### 전공정-후공정 Gap (`_find_pre_cutoff`)
```python
# (1 + min_gap_shifts)번 shift 인덱스를 뒤로 이동
# min_gap_shifts=0 → 인접 shift OK (기존 동작)
# min_gap_shifts=1 → 1 shift 공백
# min_gap_shifts=2 → 2 shift 공백 (2-shift 운영 시 ≈ 1일)
def _find_pre_cutoff(self, post_date_str, post_shift_no, min_gap_shifts) -> (date, int):
    sns = sorted(shift_nos)
    d, sno = post_date, post_shift_no
    for _ in range(1 + min_gap_shifts):
        if idx > 0: sno = sns[idx-1]
        else:       d -= 1day; sno = sns[-1]
    return d, sno
```

### 캐파 계산
- MANUAL: `UPH = UPPH × HC` (HC_Min ≤ HC ≤ HC_Max), 1 Shift 캐파 = UPH × shift_hours
- AUTO: `UPH = UPH_Fixed` (HC 고정), 동일
- 내부 단위(inner units) = SKU qty × UoM 으로 환산해서 캐파 비교

### 재고 반영 (production_needed)
```python
# _plan_so()에서 remaining 계산 시 재고 배정 수량 차감
prod_needed = AllocationRepo.production_needed(so_no, sku, line_item)
# production_needed = SO qty - allocated_inventory - actual_produced
remaining = prod_needed - already_planned
```
- `CAPACITY`: 잔여 캐파 많은 생산실 우선
- `UPH`: UPH 높은 생산실 우선

---

## UI 핵심 구조

### GanttCanvas (ui/gantt_tab.py)
- Y축 모드: `Y_MODE_ROOM` / `Y_MODE_SO` / `Y_MODE_SKU` (콤보박스로 전환)
- Shift 전개: `shift_view=True`이면 DAY_W 대신 SHIFT_W로 셀 분할
- 체크박스: 각 plan block 좌상단 11×11px. `_checked: Set[int]`로 관리
- 드래그: `mousePressEvent` → `mouseMoveEvent` → `mouseReleaseEvent`. 이동 시 사유 입력 필수
- 시각 요소: 납기 세로선(빨간 점선+SO번호), 오늘선(파란 실선), 캐파 utilization 바(상단 22px), conflict 빨간 점(좌상단 9px), FINAL 오렌지 뱃지, 금색 테두리(콘솔리데이션 그룹), 잠금 아이콘
- 컨텍스트 메뉴: 잠금/해제, 스플릿, 풀아웃, 콘솔리데이션 해제, 메모, 삭제, 하드블락

### 콘솔리데이션 (ConsolidationEngine)
- 조건: 같은 SKU + 같은 room_code + 같은 process_name
- 같은 Shift 내 → qty 합산(하나로 병합)
- 다른 Shift → 연속 Shift로 재배치
- 결과: `consolidation_group` UUID 공유, `is_locked=1` 자동 설정
- 해제: `break_group(group_id)` → is_consolidated=0, is_locked=0

### 탭 분리 (DetachedWindow)
- 탭 헤더 **우클릭** → "Open in New Window" 또는 **Ctrl+N** (현재 탭 분리)
- 탭 위젯 자체를 `DetachedWindow`로 이동 — 같은 프로세스, 같은 DB, 같은 시그널
- 분리된 창은 **10초마다 자동 새로고침** (`QTimer` → `refresh()`)
- 분리된 창을 닫으면 **원래 탭 위치로 자동 재도킹** (insertTab at original_index)
- 메인 창 닫으면 분리된 창 모두 자동 닫힘
- 상태바 우측에 분리된 창 수 표시 ("🗗 N detached")
- `notify()`가 메인 창 + 모든 분리 창 상태바에 동시 표시

| 클래스/메서드 | 역할 |
|---|---|
| `RightClickTabBar` | QTabBar 서브클래스, 우클릭 컨텍스트 메뉴 |
| `DetachedWindow` | 분리된 탭을 담는 QMainWindow, 10s 자동 새로고침, 닫힐 때 재도킹 |
| `_detach_tab(idx)` | 탭을 tab bar에서 제거 후 DetachedWindow에 삽입 |
| `_detach_current_tab()` | Ctrl+N 핸들러 — 현재 활성 탭 분리 |
| `closeEvent` | 메인 창 종료 시 모든 DetachedWindow 닫기 |

### ReleaseReportTab (ui/remaining_tabs.py)
- SO / SKU / LineItem별 예상 릴리즈 일정 리포트
- **Release Date = 마지막 공정(is_final_seq=1) 완료일 + SKU.post_lead_days**
- 상태 분류: `ON TIME` / `AT RISK` (due까지 3일 이내) / `LATE` (release > due) / `NOT PLANNED`
- 필터: 검색(SO/SKU/Line), 상태별, 납기 N일 이내
- Days to Due 컬럼: 음수=LATE(빨강), 0~3=AT RISK(노랑), 양수=ON TIME(초록)
- Excel 내보내기 (`📥 Export`) — 상태별 배경색 포함
- 10초 자동 새로고침 (DetachedWindow에서도 동작)

### InventoryTab (ui/remaining_tabs.py)
- 재고 LOT 목록 (FEFO 정렬, qty_available/allocated/remaining 표시)
- `🔗 Allocate to SO` → InventoryAllocationDialog
  - SO 선택 → FEFO 자동 제안 → 수량 수동 조정 가능 → 컨펌
  - 수동 LOT 추가 (manual override combo)
  - 유효성 검사: 배정 수량 > lot remaining 시 경고
- SO Allocation History 테이블 + 배정 취소(deallocate)
- 상태: AVAILABLE / ALLOCATED / CONSUMED / EXPIRED

### LotSampleDialog (ui/remaining_tabs.py)
- 컬럼: Actual ID / Type / Code / SO/Line / LOT / Actual Qty / **Sample Qty ✏** / **Reject Qty ✏** / Net Qty / Note
- Net Qty = Actual - Sample - Reject
- Net < SO qty → 빨간 배경 + "⚠ short N" 표시
- 저장 시 기존 lot_sample 삭제 후 재삽입 (단순 upsert)

---

## 주요 설계 결정 및 이유

| 결정 | 이유 |
|---|---|
| `init_db()` → main.py 최상단 | scheduler 싱글톤이 임포트 시점에 ShiftRepo.all() 호출하는 타이밍 문제 방지 |
| DB_PATH = `os.path.abspath(__file__)` 기준 | CWD와 무관하게 항상 프로젝트 루트의 planner.db를 가리킴 |
| Lazy Proxy 패턴 (scheduler) | 임포트 순서에 무관하게 안전한 DB 접근 보장 |
| CRP는 Excel, 나머지는 SQLite | CRP는 플래너가 Excel로 편집하는 워크플로우 유지 |
| Repository 패턴 | 백엔드를 SQLite → Excel로 교체할 때 Repository 클래스만 교체 |
| 재고-SO 배정 N:N | 하나의 LOT가 여러 SO에, 하나의 SO가 여러 LOT에서 충당 가능 |
| FEFO 자동 제안 + 플래너 컨펌 | expiry_date 오름차순 자동 제안, 플래너가 수량 조정 or 다른 LOT 선택 가능 |
| production_needed = SO qty - allocated - actual | 재고 배정 후 부족분만 생산계획에 반영 |
| entity_type 컬럼 (production_plan, lot_sample) | SKU 계획과 Material 계획을 동일 테이블에서 관리 |
| `min_gap_shifts` (INTEGER, Shift 단위) | 시간 단위는 플래너가 컨트롤 불가능. Shift 단위가 실제 계획 단위와 일치 |
| `changeover_shifts` (room_master, INTEGER DEFAULT 0) | SKU 전환 시 필요한 빈 Shift 수. 0=즉시전환, 1=1 shift 데드타임. 스케줄러가 `_placed_sku` 맵으로 인접 슬롯 추적, `_has_changeover_conflict()`로 배치 전 검사 |

---

## UI/UX 설계 규칙

**UI/UX 관련 변경(새 탭, 다이얼로그, 워크플로우 재설계 등)을 구현하기 전에 반드시 Plan 에이전트를 사용해 UX 흐름·레이아웃·인터랙션을 설계 검토하고 사용자 승인 후 구현한다.**

```
# UI 설계 검토 순서
1. Agent(subagent_type="Plan")으로 와이어프레임/플로우 설계안 도출
2. 설계안을 사용자에게 텍스트/ASCII로 제시
3. 사용자 승인 → 구현 진행
```

### 구성요소 겹침(Occlusion) 점검 체크리스트

UI를 수정하거나 새 구성요소를 추가할 때 아래를 반드시 확인한다.

| 점검 항목 | 적용 대상 |
|---|---|
| 텍스트 시작 X좌표가 왼쪽 아이콘/뱃지 우측 끝보다 큰가? | QPainter 카드, 커스텀 위젯 |
| 텍스트 Y좌표 + 높이가 다음 텍스트 라인 Y좌표와 겹치지 않는가? | 멀티라인 카드 텍스트 |
| 오른쪽 뱃지/아이콘의 X좌표가 텍스트 영역 우측 끝보다 작은가? | 잠금아이콘, 납기뱃지, FINAL뱃지 |
| 새로 추가한 라인의 Y+높이가 카드 하단(rect.bottom() - CARD_BOT_H)을 초과하지 않는가? | 추가 텍스트 라인 |
| Z-순서: 나중에 그린 요소가 앞 요소를 가리지 않는가? | 오버레이, 드래그 고스트, 충돌점 |
| QLayout 위젯이 고정 크기인 경우 폰트 크기 변경 시 클리핑 발생하지 않는가? | QLabel, QGroupBox 타이틀 |

**GanttCanvas 카드 Y좌표 기준** (변경 시 전체 라인 재검토):
```
rect.y()                          ← 카드 상단
rect.y() + PILL_MARGIN            ← 필 행 시작 (체크박스, 뱃지, 잠금)
rect.y() + CARD_TOP_H            ← 텍스트 존 시작 (= 19px)
rect.y() + CARD_TOP_H + 0        ← L1: 코드/SKU  (13px 높이)
rect.y() + CARD_TOP_H + 14       ← L2: 수량      (11px 높이)
rect.y() + CARD_TOP_H + 25       ← L3: SO번호    (10px 높이)
rect.y() + CARD_TOP_H + 35       ← L4: 납기일    (10px 높이)
rect.y() + CARD_H                ← 카드 하단 (CARD_BOT_H=0)
```

---

## 디자인 시스템 (QSS + Palette)

앱 전체에 `ui/app_style.py`의 `APP_QSS`와 QPalette가 적용되어 있다.
새 위젯을 만들 때 이 시스템을 따라야 하며, 인라인 `setStyleSheet`는 아래 예외 상황에서만 사용한다.

### 색상 토큰 (공통 기준)

| 용도 | 색상 |
|---|---|
| 앱 배경 | `#ECEEF3` |
| 카드/패널 배경 | `#FFFFFF` |
| 테두리 | `#DDE3ED` |
| 텍스트 (기본) | `#1E293B` |
| 텍스트 (보조) | `#64748B` |
| Primary (버튼 강조) | `#2563EB` |
| 성공/확인 | `#16A34A` |
| 경고 | `#D97706` |
| 오류/위험 | `#DC2626` |
| 선택 하이라이트 | `#DBEAFE` |

### 위젯별 구현 가이드

**QGroupBox — 카드처럼 사용**
```python
grp = QGroupBox("Section Title")
# 별도 setStyleSheet 불필요 — APP_QSS가 흰 배경 + 라운드 테두리 자동 적용
```

**QPushButton — 기본 vs 강조**
```python
# 일반 버튼: 스타일 지정 불필요 (흰 배경, 회색 테두리)
btn = QPushButton("Cancel")

# 주요 액션 버튼: 파란 배경
btn_confirm = QPushButton("✅ Confirm & Save")
btn_confirm.setStyleSheet(
    "background:#2563EB; color:white; font-weight:bold; "
    "border:none; border-radius:5px; padding:6px 18px;")

# 성공 액션
btn_save.setStyleSheet(
    "background:#16A34A; color:white; font-weight:bold; "
    "border:none; border-radius:5px; padding:5px 14px;")

# 위험 액션
btn_delete.setStyleSheet(
    "background:#DC2626; color:white; font-weight:bold; "
    "border:none; border-radius:5px; padding:5px 14px;")
```

**QTableWidget — 상태 셀 컬러 코딩**
```python
# Pull-in (초록)  : bg=#E8F5E9, fg=#2E7D32
# On Track (회색) : bg=#FFFFFF, fg=#555555
# Push-out 경미   : bg=#FFF8E1, fg=#F57F17
# Push-out 심각   : bg=#FFEBEE, fg=#C62828
# 편집 가능 셀    : bg=#FFFDE7 (노란빛)
# 잠금/비활성     : bg=#F5F5F5, fg=#9E9E9E
item.setBackground(QBrush(QColor("#E8F5E9")))
item.setForeground(QBrush(QColor("#2E7D32")))
```

**KPI 카드 (GroupBox 활용)**
```python
# ImpactReportTab._make_kpi() 패턴 참고
# GroupBox에 count QLabel(26px bold) + subtitle QLabel(11px) 수직 배치
# border 색상으로 상태 구분 (초록/빨강/회색)
```

**인라인 setStyleSheet 사용 금지 케이스**
- `color: green` / `color: red` 단독 사용 → 위 토큰 색상으로 대체
- OS 기본 배경(`background: white`) 단독 지정 → APP_QSS에 이미 포함
- 폰트 크기만 지정할 때는 `setStyleSheet("font-size:11px;")` 허용

**인라인 setStyleSheet 허용 케이스**
- 강조 버튼 (primary/success/danger 색상)
- 상태 표시 라벨 (info_label, banner 등)
- 셀 배경/전경 (테이블 상태 표현)
- GroupBox 카드 안에서 색상 오버라이드가 필요한 경우

---

## UI 언어 규칙

**모든 UI 문자열(버튼 레이블, 메시지박스, 컬럼 헤더, 툴팁, 상태바 메시지)은 영어로 작성한다.**
유일한 예외: `ui/help_tab.py` 내부의 HTML 설명 콘텐츠(`_ALGO_HTML`, `_MASTERS_HTML`, `_GANTT_HTML`)는 한국어 유지.

- ✅ `QMessageBox.warning(self, "Warning", "No items selected.")` 
- ❌ `QMessageBox.warning(self, "경고", "선택된 항목이 없습니다.")`
- ✅ `QLabel("Edit mode — double-click or F2 to edit")`
- ❌ `QLabel("편집 모드 — 셀을 더블클릭하거나 F2로 편집하세요")`

---

## 최근 변경 이력

### 전공정→후공정 Shift Gap 세부 조정
- **`process_routing.min_gap_shifts` 컬럼 추가** (INTEGER DEFAULT 0)
  - 0 = 인접 shift OK (기존 동작과 동일)
  - 1 = 중간에 1 shift 공백 필요
  - 2 = 2 shift 공백 (2-shift 운영 기준 ≈ 1일)
- **`_find_pre_cutoff(post_date, post_shift, min_gap_shifts)`** — shift 인덱스를 `(1 + min_gap_shifts)`번 뒤로 이동하는 정수 연산. 시간 계산 없음
- **Excel 템플릿** `04_Process_Routing.xlsx`에 `MinGapShifts` 9번째 컬럼 추가. 기존 8컬럼 파일도 하위 호환(기본값 0)
- **UI** — Process Routing 테이블에 "Min Gap (shifts)" 컬럼, 편집 다이얼로그에 `QSpinBox(suffix=" shift(s)")` 추가
- **DB 마이그레이션** — `init_db()` 내 `ALTER TABLE` 자동 실행. SQLite 3.35+ 환경에서 구 `min_gap_hours` 컬럼 자동 DROP

### 간트 차트 개선
- **체크박스 텍스트 겹침 해결** — 텍스트 시작 X를 `CB_MARGIN = CHECKBOX_S + 6 = 18px`으로 밀어서 체크박스와 텍스트 분리
- **Utilization 바 2줄** — `UTIL_H = UTIL_ROW_H * 2 = 40px`. Row1=캐파 활용율(Cap%), Row2=인력 활용율(HC%). 동일 green/orange/red 색상 기준. Y-label 측에 "Cap% / HC%" 레이블 표시
- **납기선 범위 수정** — 전체 행 관통 → 해당 SO 계획이 있는 행에만, `body_top`(util bar 아래)부터만 그림. 납기선 레이블: customer_name 있으면 이름, 없으면 SO번호
- **Customer 이름 간트 카드 표시** — SO.customer_name이 있으면 SKU코드 아래 두 번째 줄에 표시. 툴팁에도 포함
- **`_body_top()` 메서드** — `HEADER_H + UTIL_H` 반환. 간트 바디 시작 Y좌표 일원화

### 간트 비주얼 리디자인 + 성능 개선
- **모던 색상 팔레트** — `PALETTE` 12색, `HEADER_BG=QColor(38,68,128)`, 교번 행 배경(`ROW_BG_A/B`), 주말 틴트(`GRID_WEEKEND`)
- **카드 3존 분리** — `CARD_TOP_H=14`(체크박스·충돌점·FINAL뱃지) / 텍스트존 / `CARD_BOT_H=12`(잠금아이콘). `text_rect = rect.adjusted(CB_MARGIN, CARD_TOP_H, -3, -CARD_BOT_H)`
- **카드 텍스트 Y축 모드별 분기** — SKU모드: SO+Qty / 생산실모드: SKU+SO+Qty / SO모드: Qty만
- **Y_MODE_SKU 분할 Y축** — 왼쪽 `SKU_COL_W=72px`에 SKU 세로 스팬, 오른쪽 `PROC_COL_W=93px`에 공정행. `_draw_y_labels_sku()` 구현. 구분선 1px 회색(`sep_group=QColor(185,190,208)`), 내부 점선(`Qt.PenStyle.DotLine`)
- **성능** — `_row_index: Dict[str,int]`로 행 조회 O(1), `_so_rows_cache`로 납기선 사전계산, `for_groups_bulk()` 배치 SQL, `detect_conflicts()`에서 `_reload_masters()` 제거
- **간트 Excel export** — `utils/excel_io.py: export_gantt_plan()`. PlanDetail 시트(19컬럼, 상태별 셀 색상) + MaterialDemand 시트. 툴바 `📥 Export` 버튼

### SO 분할 뷰
- **`SplitSODialog`** (`ui/so_tab.py`) — 원본 SO 수량을 복수 LineItem으로 분할
  - 원본 행: Line Item 잠금(파란 배경), Qty/Due/Priority/Note 수정 가능
  - 신규 행 자동 채번: `{원본LineItem}-2`, `-3` …
  - Remaining 실시간 표시 — 0이 될 때만 Split 버튼 활성화
  - 확정 시 각 행을 `SORepo.upsert()`로 저장
- **컨텍스트 메뉴** — OPEN/HOLD SO에 `✂ Split` 추가
- **버그 수정** — status 컬럼 인덱스 8→9 (Customer 컬럼 추가 이후 밀렸던 오류)

### SO 마스터 customer_name 추가
- `sales_order` 테이블 `customer_name TEXT` 컬럼 추가
- `SORepo.upsert()` INSERT/UPDATE에 customer_name 포함
- SO 탭 테이블에 Customer 컬럼 추가 (4번째)
- SO 편집 다이얼로그에 Customer 입력 필드 추가

### 마스터 인라인 편집 (옵션B)
- `_make_table(editable=True)` — DoubleClick/F2로 셀 직접 편집
- `itemChanged` 시그널로 변경된 셀 노란 배경 표시 + "N row(s) modified" 카운터
- `💾 Save Changes` 버튼 클릭 시 일괄 DB 저장, 저장 성공 셀은 초록 배경
- `_loading` 플래그로 데이터 로드 중 itemChanged 이벤트 무시
- 현재 SKUMasterWidget에 적용. 다른 마스터 위젯은 향후 동일 패턴 적용 가능

### SO 업로드 미리보기 (SOUploadPreviewDialog)
- **`SOUploadPreviewDialog`** (`ui/so_tab.py`) — SO Excel 업로드 전 변경 내용 diff 표시
  - NEW / MODIFIED / CLOSED 3가지 변경 유형별 탭으로 구분
  - MODIFIED: 변경된 필드만 Old→New 형태로 강조 표시
  - 확정(Confirm) 시 일괄 upsert + `so_history` 기록 + 스냅샷 저장
  - SO 탭 "Upload SO" 버튼 → 파일 선택 → 미리보기 → 확정 흐름

### 생산실별 인력 자동배정 UI (HCDemandDialog)
- **`HCDemandDialog`** (`ui/remaining_tabs.py`) — 생산계획 기반 Shift별 필요 HC 자동 계산 후 CRP Excel 업데이트
  - `Scheduler.compute_hc_distribution_preview()` 호출 → (날짜, Shift, 생산실, 공정)별 필요 HC 계산
  - 체크박스 테이블로 적용할 항목 선택
  - 확정 시 `CRPManager.write_hc_values()` 로 CRP Excel 직접 업데이트
  - CRP 탭 `🤖 Auto-fill HC` 버튼으로 실행

### 리플래닝 리포트 (replan_after_actuals + ReplanReportDialog)
- **`Scheduler.replan_after_actuals()`** (`core/scheduler.py`) — 실적 입력 후 계획 재조정
  - 완전 완료 SO: 관련 미잠금 계획 전량 삭제
  - 부분 완료 SO: 잔여 수량(`production_needed`) 기준으로 재계획 실행
  - 오류(캐파 부족 등)는 report["late"]에 누적
- **`ReplanReportDialog`** (`ui/remaining_tabs.py`) — 3탭 결과 리포트
  - Deleted 탭: 삭제된 계획 목록 (SO/SKU/수량)
  - Re-planned 탭: 재계획된 SO 목록
  - Errors 탭: 재계획 실패 항목 + 사유
  - Actuals 탭 "🔄 Re-Plan After Actuals" 버튼으로 실행

### 3Shift 운영 캘린더 UI (CalendarWidget)
- **`CalendarWidget`** (`ui/master_tab.py`) — Masters > Calendar 탭
  - 날짜 × (생산실 + Shift) 그리드. 셀 클릭 → Open/Closed 토글
  - 우클릭 → Hold 설정 (is_hold=1, 주황 배경)
  - 일괄 버튼: Open Weekdays / Close Weekends / Open All / Close All
  - `CalendarRepo.set_slot()` / `get_slot()` 로 DB 반영
  - Shift가 3개이면 자동으로 3열 표시 (Third Shift 지원)

### 콘솔리데이션 체크박스 UI 완성
- **히트 영역 확대** — 시각 크기 11×11px 유지, 클릭 히트 영역은 `_check_hit_rects`로 20×20px 별도 관리
- **체크박스 → 콘솔리데이션 다이얼로그 흐름** — 2개 이상 체크 후 툴바 "🔗 Consolidate" 버튼 활성화
- **ConsolidationEngine** — 같은 SKU + room + process 조건 검증 후 병합 or 연속 Shift 재배치, `consolidation_group` UUID 공유, `is_locked=1` 자동 설정

### AddPlanDialog (수동 계획 추가)
- **`AddPlanDialog`** (`ui/gantt_tab.py`) — 간트 차트에서 빈 셀 우클릭 → "➕ Add Plan"
  - SO / SKU / Line Item / 공정 / 수량 입력
  - `is_final_seq` 자동 판단 (routing 조회)
  - Y_MODE_ROOM에서만 접근 가능

### AlertsTab / DashboardTab
- **`AlertsTab`** (`ui/remaining_tabs.py`) — 캐파시티 초과 충돌, 납기 지연 SO, QC 부적합(net_qty < SO qty) 3종 경고 목록. 10초 자동 새로고침
- **`DashboardTab`** (`ui/remaining_tabs.py`) — OPEN SO 상태 요약 카드(총 SO 수, 언플랜드, AT RISK, LATE), 주별 생산 완료 진행 현황 테이블

### 언플랜드 오더 — 공정 단위 뷰
- **`SORepo.unplanned()` 재작성** — 최종공정(`is_final_seq=1`)만 보던 방식 → SKU의 **모든 routing step**을 순회
  - 각 step별 `planned_qty_for_step < production_needed`이면 행으로 반환
  - 중간공정 삭제 시에도 해당 step이 언플랜드로 표시됨
  - routing 미정의 SO는 "(no routing)" 단일 행으로 표시
- **`PlanRepo.planned_qty_for_step(so, sku, line, process_seq)`** 신규 — 특정 공정 seq의 계획 수량 합산
- **UI** — 언플랜드 패널 테이블 6열→7열: `["SO","SKU/Line","Customer","Process","Remaining","Due","Pri"]`
  - Process 컬럼 형식: `[seq] 공정명`, 최종공정은 주황색
  - 패널 폭 340→440px, 카운터 "N unplanned step(s)"

### 간트 드래그 — 생산실 이동 + 공정 미지원 시각 경고
- **생산실(room) 이동 지원** — 드래그 시 x축(날짜/Shift)뿐 아니라 y축(행) 기반으로 `room_code`도 업데이트 (`Y_MODE_ROOM`에서만)
- **드래그 중 유효성 실시간 검사** — `_room_proc_set: Set[(room_code, process_name)]` 캐시 (`load_data` 시 room_master에서 빌드, O(1) 조회)
- **빨간 고스트** — 타겟 생산실이 해당 공정을 지원하지 않으면 고스트 사각형이 빨간색 + "✕ Not supported" 텍스트 표시
- **드롭 차단** — `_drag_invalid=True`인 상태에서 마우스를 놓으면 경고 다이얼로그만 표시, DB 업데이트 없음
- **변경 필드** — 유효한 이동 시 `plan_date`, `shift_no`, (필요시) `room_code` 3개 필드 업데이트

### 체인지오버 타임 (Changeover Time)
- **`room_master.changeover_shifts` 컬럼 추가** (INTEGER DEFAULT 0) — 같은 Room/Process에서 다른 SKU로 전환 시 필요한 빈 Shift 수
- **스케줄러 변경** (`core/scheduler.py`)
  - `_changeover_shifts: Dict[(room_code, process_name), int]` — `_reload_masters()`에서 빌드
  - `_placed_sku: Dict[(room_code, process_name, date_str, shift_no), entity_code]` — 배치된 슬롯 추적. `_build_slot_map()`에서 locked plan으로 초기화
  - `_next_slot(ds, sno)` — 다음 shift 반환 (날짜 넘어가는 경우 처리)
  - `_has_changeover_conflict(room, proc, ds, sno, co_shifts, entity)` — 직후 `co_shifts`개 슬롯 안에 다른 entity가 있으면 True
  - `_record_placed(room, proc, ds, sno, entity)` — 배치 후 `_placed_sku` 기록
  - `_plan_so()` / `_place_material_plans()` 배치 루프에 체인지오버 검사 + 기록 추가
- **DB 마이그레이션** — `init_db()` 내 `ALTER TABLE room_master ADD COLUMN changeover_shifts` 자동 실행
- **UI** — Room/Process 테이블에 "Changeover (shifts)" 컬럼, 편집 다이얼로그에 `QSpinBox(suffix=" shift(s)", range=0~20)` 추가
- **Excel** — `03_Room_Process_Master.xlsx` 템플릿에 `Changeover_Shifts` 10번째 컬럼 추가. 기존 10컬럼 파일도 하위 호환 (기본값 0)
- **향후 확장**: 나중에 `changeover_matrix(room_code, process_name, from_sku, to_sku, changeover_shifts)` 테이블로 SKU 조합별 세분화 가능

### 기타
- **탭 전환 피드백** — `_on_tab_changed()`: WaitCursor → refresh() → restoreOverrideCursor(). 상태바 "Loading…" 표시
- **CRP 경로 미설정 안내** — CRP 탭 refresh() 시 경로 미설정/파일 없으면 노란 배너 표시
- **AUTO 공정 HC=0 버그 수정** — AUTO도 CRP에 `hc_fixed`만큼 인원이 있어야 슬롯 활성화

---

## TODO

- [x] **SO 분할 뷰** — `SplitSODialog` 구현 완료. SO 탭 우클릭 → ✂ Split
- [x] **엑셀 업로드 시 SO 변경 미리보기** — `SOUploadPreviewDialog` 구현 완료. NEW/MODIFIED/CLOSED diff 표시
- [x] **콘솔리데이션 체크박스 UI 완성** — `_check_hit_rects` 별도 관리로 히트 영역 11×11→20×20px 확대. 시각 크기는 유지
- [x] **생산실별 인력 자동배정 UI** — CRP 탭 `🤖 Auto-fill HC` 버튼. `HCDemandDialog`에서 계획 기반 필요HC 계산 → 체크박스로 선택 → CRP Excel write (`crp_manager.write_hc_values()`)
- [x] **리플래닝 리포트 상세화** — `replan_after_actuals()` 완전 완료 SO 삭제 + 부분완료 SO 재계획 포함. `ReplanReportDialog`에서 삭제/재계획/오류 3탭으로 결과 표시
- [x] **3Shift 운영 캘린더 UI** — Masters > Calendar 탭 추가. `CalendarWidget`: 날짜×(생산실+Shift) 그리드, 클릭 Open/Closed 토글, 우클릭 Hold, 일괄 버튼(Open Weekdays / Close Weekends / Open All / Close All)
- [x] **전공정→후공정 Shift gap 세부 조정** — `process_routing.min_gap_shifts` (Shift 단위 정수). `_find_pre_cutoff()` 구현. 0=인접 OK, 1=1 shift 공백, 2≈1일
- [ ] **PyInstaller EXE 빌드 스크립트** — `pyinstaller --onefile --windowed --name ProductionPlanner main.py`로 기본 빌드 가능하나 openpyxl 데이터 파일 포함 옵션 추가 필요

---

## Excel 입력 파일 목록 (샘플 파일: 01~07_*.xlsx)

| 파일 | 시트명 | 업로드 위치 |
|---|---|---|
| 01_SKU_Master.xlsx | SKUMaster | Masters > SKU Master |
| 02_Material_Master.xlsx | MaterialMaster | Masters > Material Master |
| 03_Room_Process_Master.xlsx | RoomMaster | Masters > Room/Process |
| 04_Process_Routing.xlsx | ProcessRouting | Masters > Process Routing |
| 05_Sales_Order.xlsx | SalesOrders | Sales Orders > Upload SO |
| 06_CRP.xlsx | CRP + HOLD | App Config에서 경로 설정 후 Refresh |
| 07_Inventory.xlsx | Inventory | 📦 Inventory > Upload Inventory |

권장 업로드 순서: `03 Room` → `02 Material` → `01 SKU` → `04 Process Routing` → `05 SO` → `06 CRP`

---

## 자주 쓰는 Claude Code 명령

```bash
# 실행 및 테스트
python main.py
python -c "from data.database import init_db; init_db(); print('OK')"

# 구문 검사
python -m py_compile core/scheduler.py ui/master_tab.py

# 복수 파일 구문 검사
python -m py_compile data/database.py data/repositories.py core/scheduler.py utils/excel_io.py ui/master_tab.py
```
