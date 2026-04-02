from flask import Flask, request, jsonify
import sqlite3
from datetime import datetime, timedelta

app = Flask(__name__)

def get_connection():
    return sqlite3.connect("team_ai.db")

@app.route("/callback", methods=["POST"])
def mpesa_callback():

    data = request.json

    try:
        result = data["Body"]["stkCallback"]

        if result["ResultCode"] == 0:

            metadata = result["CallbackMetadata"]["Item"]

            phone = None
            amount = None

            for item in metadata:
                if item["Name"] == "PhoneNumber":
                    phone = str(item["Value"])
                if item["Name"] == "Amount":
                    amount = item["Value"]

            conn = get_connection()

            # 🔥 FIND ORG BY PHONE
            org = conn.execute("""
                SELECT name FROM organizations WHERE phone=?
            """, (phone,)).fetchone()

            if org:

                org_name = org[0]

                expiry = datetime.now() + timedelta(days=30)

                # ✅ ACTIVATE ORG
                conn.execute("""
                UPDATE organizations
                SET status='active',
                    expires_at=?,
                    last_payment=?
                WHERE name=?
                """, (str(expiry), str(datetime.now()), org_name))

                # ✅ SAVE PAYMENT
                conn.execute("""
                INSERT INTO payments(organization,amount,phone,date)
                VALUES (?,?,?,?)
                """, (org_name, amount, phone, str(datetime.now())))

                conn.commit()

            conn.close()

        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})

    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    app.run(port=5000)
