# Production Planner

로컬 실행 Python 생산계획 프로그램입니다.

## 설치 및 실행

```bash
# 1. 가상환경 생성 (권장)
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux

# 2. 패키지 설치
pip install PyQt6 openpyxl

# 3. 실행
cd production_planner
python main.py
```

## EXE 배포 빌드

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name ProductionPlanner main.py
# dist/ProductionPlanner.exe 생성됨
```

## 파일 구조

```
production_planner/
├── main.py                  # 진입점
├── planner.db               # SQLite DB (자동 생성)
├── requirements.txt
│
├── data/
│   ├── database.py          # DB 초기화 & 스키마
│   ├── repositories.py      # 데이터 접근 레이어 (Repository 패턴)
│   └── crp_excel.py         # CRP Excel 연동
│
├── core/
│   └── scheduler.py         # 스케줄링 엔진
│
├── ui/
│   ├── main_window.py       # 메인 윈도우 + 탭 구조
│   ├── gantt_tab.py         # 간트 차트 (메인 플래닝 뷰)
│   ├── so_tab.py            # SO 관리
│   ├── master_tab.py        # 마스터 관리 (SKU/생산실/Shift/Config)
│   ├── crp_tab.py           # CRP 현황
│   ├── actuals_tab.py       # 생산실적 입력
│   ├── alerts_tab.py        # 알림/충돌 현황
│   └── dashboard_tab.py     # 대시보드
│
└── utils/
    └── excel_io.py          # Excel 임포트/익스포트
```

## 주요 기능

| 기능 | 설명 |
|------|------|
| **간트 차트** | Y축 생산실/SO/SKU 전환, Shift 단위 전개, 드래그 이동, 잠금 |
| **자동 배정** | 우선순위→FIFO 순서, 납기역방향 채움, 캐파 기반 배분 |
| **Pull Forward** | 유효기간 상한 내에서 앞당겨 생산 제안 |
| **납기선 표시** | 간트에 SO별 납기 세로선 + SO번호 |
| **CRP Excel** | 날짜/Shift/생산실별 인원 Excel 연동, 실시간 새로고침 |
| **SO 업로드** | 변경 감지(신규/수정/삭제/클로즈), 스냅샷 기반 롤백 |
| **실적 입력** | Shift별 SO-LineItem 단위 입력, LOT 매칭 |
| **알림 패널** | 캐파 충돌 빨간 점, 납기 미준수 SO 목록 |
| **마스터 관리** | SKU/생산실/Shift Excel 업로드 + 화면 직접 편집 |

## 데이터 저장

- **SQLite** (`planner.db`): SO, 생산계획, 마스터, 실적, 히스토리
- **Excel** (`CRP_*.xlsx`): 날짜별 인원 캐파 (CRP)

> SQLite → Excel 전환이 필요할 경우 `data/repositories.py`의 Repository 클래스만 교체하면 됩니다.
