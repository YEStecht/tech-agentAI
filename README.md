# tech-agentAI

## 9단계: Google 로그인 및 교사 권한 설정

학생 앱(`app.py`)은 로그인 없이 공개 실습만 제공합니다. 교사 앱(`teacher.py`)은
Firebase Authentication의 Google ID 토큰을 서버에서 검증한 뒤 Firestore의
`teacher_permissions` 허용 목록을 매 보호 요청마다 확인합니다. 기존 공유 비밀번호
`TEACHER_PASSWORD`는 더 이상 인증에 사용하지 않습니다.

### Firebase Console

1. **빌드 > Authentication > Sign-in method > Google**을 사용 설정합니다.
2. **빌드 > Authentication > Settings > Authorized domains**에 배포 도메인과
   로컬 테스트용 `localhost`가 있는지 확인합니다.
3. **프로젝트 설정(톱니바퀴) > 일반 > 내 앱 > 웹 앱 > SDK 설정 및 구성**에서
   아래 웹 설정 값을 확인합니다. 서비스 계정 키와 혼동하지 마세요.

### Streamlit 설정

`tech_agent/.streamlit/secrets.toml` 또는 같은 이름의 환경변수에 다음을 설정합니다.
값은 저장소에 커밋하지 않습니다.

```toml
ADMIN_EMAIL = "관리자 Google 이메일"
FIREBASE_API_KEY = "웹 앱 apiKey"
FIREBASE_AUTH_DOMAIN = "웹 앱 authDomain"
FIREBASE_PROJECT_ID = "웹 앱 projectId"
FIREBASE_APP_ID = "웹 앱 appId"
```

기존 `OPENAI_API_KEY`와 Firebase Admin SDK 서비스 계정 설정은 그대로 유지합니다.
`ADMIN_EMAIL`이 없으면 모든 교사 접근이 차단됩니다. 설정된 관리자는 교사 목록에서
비활성화하거나 삭제할 수 없으며, 실제 교사는 관리자가 **교사 계정 관리**에서 이메일과
학교 식별자를 등록해야 합니다. 같은 학교 식별자를 가진 승인 교사만 새로 만든 실습을
공동 관리합니다. 학교 정보가 없는 기존 실습은 관리자만 관리할 수 있습니다.

### 실제 로그인 확인

1. `tech_agent/.venv/Scripts/streamlit run tech_agent/teacher.py`를 실행합니다.
2. 미등록 Google 계정이 차단되고 로그아웃이 가능한지 확인합니다.
3. 관리자 계정으로 로그인해 테스트 교사를 등록한 뒤, 해당 교사가 접근 가능한지 확인합니다.
4. 교사를 비활성화 또는 삭제하고 다음 요청/새로고침에서 즉시 차단되는지 확인합니다.
5. 로그아웃 후 뒤로가기와 새로고침에서도 교사 화면이 열리지 않는지 확인합니다.

테스트가 끝나면 테스트 교사 권한은 관리자 화면에서 삭제합니다. 운영 Firestore 데이터와
Security Rules는 자동 테스트에서 변경하지 않습니다.
