import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

load_dotenv()

# Setup in .env
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

def send_verification_email(to_email: str, token: str, backend_url: str = "http://localhost:8000"):
    """
    Sends an email with the verification link.
    If credentials are not found, logs to console for testing.
    """
    verification_url = f"{backend_url}/auth/verify-email?token={token}"
    
    subject = "Verify Your Disaster360 Account"
    body = f"""
    <html>
        <body>
            <h2>Welcome to Disaster360!</h2>
            <p>Please click the button below to verify your email address and activate your account.</p>
            <br>
            <a href="{verification_url}" style="padding: 10px 20px; background-color: #f15a29; color: white; text-decoration: none; border-radius: 5px;">Verify Email</a>
            <br><br>
            <p>Or copy and paste this link into your browser:</p>
            <p>{verification_url}</p>
            <br>
            <p>This link will expire in 30 minutes.</p>
        </body>
    </html>
    """

    if not EMAIL_USER or not EMAIL_PASS:
        print("\n" + "="*50)
        print("WARNING: SMTP Credentials not configured in .env")
        print(f"Mock Email sent to: {to_email}")
        print(f"Verification URL: {verification_url}")
        print("="*50 + "\n")
        return True

    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
            
        print(f"Verification email sent to {to_email}")
        return True
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        return False
