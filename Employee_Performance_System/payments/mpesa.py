import requests
from datetime import datetime
import base64

# ======================
# CONFIG (Safaricom)
# ======================
CONSUMER_KEY = "YOUR_KEY"
CONSUMER_SECRET = "YOUR_SECRET"
SHORTCODE = "174379"
PASSKEY = "YOUR_PASSKEY"

def get_access_token():
    if CONSUMER_KEY == "YOUR_KEY" or CONSUMER_SECRET == "YOUR_SECRET":
        raise ValueError("M-Pesa credentials not configured. Please set CONSUMER_KEY and CONSUMER_SECRET in payments/mpesa.py")
    
    url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    try:
        response = requests.get(url, auth=(CONSUMER_KEY, CONSUMER_SECRET), timeout=10)
        response.raise_for_status()
        data = response.json()
        if 'access_token' not in data:
            raise ValueError(f"M-Pesa API response missing access_token: {data}")
        return data['access_token']
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Failed to connect to M-Pesa API: {str(e)}")
    except ValueError as e:
        raise e
    except Exception as e:
        raise RuntimeError(f"M-Pesa authentication error: {str(e)}")

def stk_push(phone, amount):
    if SHORTCODE == "174379" and PASSKEY == "YOUR_PASSKEY":
        raise ValueError("M-Pesa SHORTCODE or PASSKEY not configured. Please update payments/mpesa.py with valid Safaricom credentials")
    
    try:
        access_token = get_access_token()

        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password = base64.b64encode((SHORTCODE + PASSKEY + timestamp).encode()).decode()

        url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        payload = {
            "BusinessShortCode": SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": SHORTCODE,
            "PhoneNumber": phone,
            "CallBackURL": "http://YOUR_SERVER_IP:5000/callback",
            "AccountReference": "TeamSystem",
            "TransactionDesc": "Subscription Payment"
        }

        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        
        return response.json()
    
    except ValueError as e:
        raise ValueError(str(e))
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"M-Pesa API request failed: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"STK Push error: {str(e)}")