#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zabbix Ai E-mail By Shoulham 2026-5-27
对zabbix告警进行AI辅助分析
"""

import sys
import requests
import smtplib
from email.mime.text import MIMEText

# --------------------------大模型和邮箱配置 -------------------------
VLLM_API_URL = "http://大模型服务器IP:端口/v1/chat/completions"
MODEL_NAME = "模型名称"
API_KEY = "大模型授权API Key"
SMTP_SERVER = "smtp.exmail.qq.com"
#邮箱服务器地址，可以换成其他邮箱的，用企业邮的好处就是支持一天无限制发送
SMTP_PORT = 465
SMTP_USER = "填写用于发送邮箱地址"
SMTP_PASS = "填写用于发送邮箱密码"
MAIL_FROM = "Zabbix告警 "
# ---------------------------------------------------------------------------

def call_vllm(alert_text):
    """调用本地vLLM生成AI辅助解决方案"""
    prompt = f"""
下面是Zabbix告警内容，请给出：
1. 故障原因分析
2. 排查步骤
3. 临时解决方案

告警内容：
{alert_text}
    """.strip()

    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 4096
    }
    try:
        # 超时30秒，给AI足够时间生成内容
        r = requests.post(VLLM_API_URL, json=payload, timeout=120)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"⚠️ AI分析失败：{str(e)}"

def send_email(to_list, subject, body):
    """发送HTML格式邮件（适配QQ企业邮箱465端口SSL）"""
    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = ", ".join(to_list)

    try:
        # 465端口直接用SMTP_SSL
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
    except Exception as e:
        print(f"❌ 邮件发送失败：{e}")

if __name__ == "__main__":
    # Zabbix 传参格式：脚本 收件人 主题 告警原文
    if len(sys.argv) != 4:
        print("用法：python3 ai_email.py 收件人邮箱 邮件主题 告警原文")
        sys.exit(1)

    to_email = sys.argv[1]
    subject = sys.argv[2]
    alert_body = sys.argv[3]

    # 1. 调用AI生成解决方案
    ai_solution = call_vllm(alert_body)

    # 2. 拼接最终邮件内容（宏变量用{{}}转义，让Zabbix后续解析）
    final_html = f"""
{alert_body}

<hr style="border:1px solid #eee; margin:20px 0;">
<h3 style="color:#2c3e50;">🤖 AI 辅助解决方案 </h3>
<pre style="background:#f5f5f5; padding:15px; border-radius:5px; white-space:pre-wrap; word-wrap:break-word;">{ai_solution}</pre>
    """.strip()

    # 3. 发送邮件
    send_email([to_email], subject, final_html)
