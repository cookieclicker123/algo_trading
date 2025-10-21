# 🚀 **IBKR Gateway Setup for Live Trading**

## **📋 Prerequisites:**

✅ You have IBKR Gateway installed  
✅ You have a funded IBKR account with £2000  
✅ You're ready to place real trades  

## **🔧 IBKR Gateway Configuration:**

### **Step 1: Start IBKR Gateway**

```bash
# Start IBKR Gateway (not TWS)
# Make sure it's running on port 7497
```

### **Step 2: Configure Gateway Settings**

In IBKR Gateway:

1. **API Settings:**
   - ✅ Enable Active X and Socket Clients
   - ✅ Read-Only API: **FALSE** (must be false for trading)
   - ✅ Socket Port: **7497**
   - ✅ Trusted IPs: Add your local IP (127.0.0.1)

2. **Trading Permissions:**
   - ✅ Enable trading
   - ✅ Enable market data
   - ✅ Enable order placement

### **Step 3: Verify Connection**

Gateway should show:
- ✅ "API connection established"
- ✅ Port 7497 listening
- ✅ Your account logged in

## **🧪 Integration Test Process:**

### **Step 1: Run the Integration Test**

```bash
cd /Users/seb/dev/newsflash
source .venv/bin/activate
python tests/test_full_integration.py
```

### **Step 2: Test Workflow**

The test will:
1. ✅ Create IMMINENT news about AAPL
2. ✅ Simulate Telegram trading options
3. ✅ Execute real $100 AAPL trade
4. ✅ Verify trade appears in IBKR account

### **Step 3: Verification**

After test runs:
1. **Open IBKR Desktop/TWS**
2. **Check Positions** → Look for new AAPL shares
3. **Check Orders** → Verify $100 order filled
4. **Confirm Success** → Type "yes" in test

## **🎯 Expected Results:**

**In IBKR Account:**
- 📊 New AAPL position
- 💰 ~$100 investment
- 📈 Market order filled
- ⏰ Timestamp of execution

**In Test Output:**
- ✅ All steps completed
- ✅ Trade execution successful
- ✅ Verification confirmed

## **🔄 After Successful Test:**

Once the integration test passes:

1. **Sell the AAPL shares** (to get back to original state)
2. **Enable live system** (already done - enabled=True)
3. **Start the news server** for 24/7 operation
4. **Wait for real IMMINENT news** and trade automatically!

## **🚨 Important Notes:**

### **Safety Features:**
- ✅ **$100 fixed amount** per trade
- ✅ **30-minute timeout** for decisions
- ✅ **Only IMMINENT news** triggers trading
- ✅ **Manual verification** required

### **Monitoring:**
- ✅ **IBKR Desktop** shows all trades
- ✅ **Telegram** confirms trade execution
- ✅ **Logs** record all activity

### **Risk Management:**
- ✅ **Rare triggers** (1-2 IMMINENT per day)
- ✅ **Small amounts** ($100 per trade)
- ✅ **Human oversight** (you approve each trade)

## **🎉 Ready for Live Trading!**

Once the integration test passes, your system is **production-ready**:

- 🤖 **Fully automated** news detection
- 📱 **Telegram interface** for decisions
- 💰 **Real IBKR trading** execution
- 🛡️ **Safety controls** built-in

**Your autonomous trading system is ready to make money!** 🚀
