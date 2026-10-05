# Nginx · Certbot Docker 구성

기존 API·DB가 같은 EC2에서 실행 중인 상태에서 적용합니다.
API Compose의 `guitar-practice-db_default` 네트워크와 `api:8000`을 사용합니다.
이 폴더만으로 API·DB를 생성하지는 않습니다. 아래 명령은 각 단계가 성공하면
다음으로 진행하고, 오류가 발생하면 멈춥니다.

## 1. 서버 준비

이 폴더를 서버에 받은 실제 위치에 맞게 아래 경로를 조정합니다.
AWS 콘솔에서 현재 공인 IPv4를 확인합니다. IP가 바뀌면 인증서 재발급이 필요합니다.
보안 그룹의 TCP 80은 인증서 검증을 위해 인터넷에서 접근 가능해야 합니다.
TCP 443은 앱을 사용할 클라이언트에 허용합니다. 8000·5432는 공개하지 않습니다.
호스트 방화벽을 사용한다면 동일한 접근이 가능해야 합니다.

```bash
cd ~/Guitar_Practice_Support_SW/nginx
# 기존 설정은 보존합니다.
test -f .env || cp .env.example .env
sudo docker network inspect guitar-practice-db_default --format '{{.Name}}'
sudo docker compose config -q
# 서비스를 중지하기 전에 필요한 이미지를 준비합니다.
sudo docker compose --profile tools pull
# 호스트 포트를 사용하지 않는 일회성 컨테이너로 설정을 먼저 검사합니다.
sudo docker compose run --rm --no-deps nginx nginx -t
curl -f http://127.0.0.1:8000/health/
curl -f http://127.0.0.1:8000/health/db/
```

API의 `DJANGO_ALLOWED_HOSTS`에는 현재 공인 IP와 `127.0.0.1,localhost`를 포함합니다.
변경한 경우 API 폴더에서 `sudo docker compose up -d`로 반영합니다.

## 2. HTTP Nginx를 컨테이너로 전환

```bash
# 호스트 Nginx와의 80 포트 충돌을 방지합니다.
sudo systemctl stop nginx
sudo docker compose up -d nginx
sudo docker compose exec -T nginx nginx -t
curl -f http://127.0.0.1/health/
curl -f http://127.0.0.1/health/db/
```

실패하면 `sudo docker compose logs --tail 50 nginx`로 확인합니다.
원상복구는 `sudo docker compose stop nginx` 후 `sudo systemctl start nginx`입니다.

## 3. 공인 IP 인증서 발급

```bash
# 실제 현재 공인 IPv4를 입력합니다.
read -r -p 'EC2 public IPv4: ' PUBLIC_IP
# 약관·연락처 관련 질문을 확인하고 진행합니다.
# backend는 도메인이 아닌 인증서 저장 이름입니다.
sudo docker compose --profile tools run --rm certbot certonly \
  --webroot --webroot-path /var/www/certbot \
  --preferred-profile shortlived \
  --ip-address "$PUBLIC_IP" --cert-name backend
```

발급 성공 전에는 다음 단계로 넘어가지 않습니다. IP 인증서는 6일 유효하며
자동 갱신이 필요합니다. [Let's Encrypt 공식 안내](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)

## 4. HTTPS 적용

`.env`를 열어 `NGINX_CONFIG=https.conf`로 변경합니다.

```bash
vi .env
# 실행 중인 서비스에 적용하기 전에 인증서와 설정을 검사합니다.
sudo docker compose run --rm --no-deps nginx nginx -t
sudo docker compose up -d nginx
sudo docker compose exec -T nginx nginx -t
```

로컬 PC에서 `PUBLIC_IP`를 실제 공인 IP로 바꿔 확인합니다.
인증서 검증을 생략하는 `-k` 옵션은 사용하지 않습니다.

```bash
curl -i https://PUBLIC_IP/health/
curl -i https://PUBLIC_IP/health/db/
```

둘 다 HTTP 200이면 호스트 Nginx의 부팅 자동 시작을 끕니다.

```bash
sudo systemctl disable nginx
```

## 5. 자동 갱신

```bash
# 테스트 인증기관으로 갱신 절차만 검사하며 운영 인증서를 교체하지 않습니다.
sudo docker compose --profile tools run --rm certbot renew --dry-run
sudo bash renew.sh
# 아래 일정을 root crontab에 한 번만 추가합니다.
sudo crontab -e
```

```cron
# 6시간마다 갱신 필요 여부 확인 후 Nginx에 인증서를 다시 읽게 합니다.
17 */6 * * * /bin/bash /home/ubuntu/Guitar_Practice_Support_SW/nginx/renew.sh >> /var/log/guitar-practice-certbot.log 2>&1
```

배치 경로가 다르면 cron 경로도 조정하고, 실행 후 로그에서 오류를 확인합니다.
인증서 볼륨 보존을 위해 `docker compose down -v`는 사용하지 않습니다.
앱은 처음부터 `https://현재공인IP`로 요청하도록 설정합니다.
