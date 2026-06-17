# Production Planner — Claude Code 프로젝트 컨텍스트

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
| `room_master` | 생산실/공정 (room_code+process_name PK, room_type, process_type AUTO/MANUAL) |
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
6. 반제품(MATERIAL) 수요 수집 → 납기 21일 내 묶음 → Material 역방향 배정
7. is_final_seq=1 슬롯에 `[FINAL]` 메모 + MRP 기준점 플래그

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
