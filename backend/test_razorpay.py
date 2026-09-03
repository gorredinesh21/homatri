import sys
import razorpay

# Razorpay Credentials
KEY_ID = "rzp_live_TTCnAhgfkFLtmh"
KEY_SECRET = "djYgGzvyZqh3kg2yHm1tk48X"

print("====================================================")
print("Testing Razorpay Connection & Order Generation...")
print(f"Key ID: {KEY_ID}")
print("====================================================")

try:
    client = razorpay.Client(auth=(KEY_ID, KEY_SECRET))

    # Create a test Razorpay order for ₹179 (17900 paise)
    order_data = {
        "amount": 17900,  # Amount in paise (17900 = ₹179.00)
        "currency": "INR",
        "receipt": "receipt_test_1001",
        "notes": {
            "customer_phone": "7416767453",
            "test": "Homatri Razorpay Verification"
        }
    }

    razorpay_order = client.order.create(data=order_data)

    print("🟢 RAZORPAY API TEST SUCCESSFUL!")
    print(f"Razorpay Order ID : {razorpay_order.get('id')}")
    print(f"Amount            : ₹{razorpay_order.get('amount') / 100}")
    print(f"Currency          : {razorpay_order.get('currency')}")
    print(f"Status            : {razorpay_order.get('status')}")
    print(f"Receipt           : {razorpay_order.get('receipt')}")
    print("====================================================")

except Exception as e:
    print("🔴 RAZORPAY API ERROR:")
    print(str(e))
    print("====================================================")
