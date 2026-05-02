/**
 * 1VALET AUTO-LISTENER — injected automatically by start.sh via Chrome CDP.
 * SERVER_URL is filled in at injection time — no manual editing needed.
 */
(function () {
    'use strict';

    // Filled in by inject_tab.py at startup — do not edit manually.
    const SERVER_URL = "__SERVER_URL__";

    const CONFIG = { pollInterval: 5000, autoStart: true };
    const state  = { isRunning: false, pollTimer: null, processedUnits: new Set(), isProcessing: false };

    function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

    // Build fetch headers — include ngrok bypass header only when needed
    function headers(extra) {
        const h = { 'Content-Type': 'application/json', ...extra };
        if (SERVER_URL.includes('ngrok') || SERVER_URL.includes('trycloudflare')) {
            h['ngrok-skip-browser-warning'] = 'true';
        }
        return h;
    }

    async function addUnit(unitNumber) {
        console.log(`\n📦 Adding unit: ${unitNumber}...`);
        try {
            const input = [...document.querySelectorAll('input')].find(
                inp => inp.placeholder && inp.placeholder.toLowerCase().includes('suite') && inp.offsetParent !== null
            );
            if (!input) { console.error('❌ Input field not found — is the popup still open?'); return false; }

            input.value = ''; input.focus();
            for (const char of unitNumber) {
                input.value += char;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                await sleep(50);
            }
            input.dispatchEvent(new Event('change', { bubbles: true }));
            console.log(`  ✓ Typed: ${unitNumber}`);
            await sleep(800);

            const match = [...document.querySelectorAll('div, li, span')].find(el => {
                const t = el.textContent.trim();
                return t === unitNumber && el.offsetParent !== null && el.getBoundingClientRect().height > 10;
            });

            if (match) {
                match.click();
                console.log(`  ✓ Selected: ${unitNumber}`);
                console.log('✅ Unit added!\n');
            } else {
                console.log('  ⚠️ Trying Enter key...');
                input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }));
                await sleep(300);
                console.log('✅ Enter pressed\n');
            }

            await sleep(1000);
            const refocus = [...document.querySelectorAll('input')].find(
                inp => inp.placeholder && inp.placeholder.toLowerCase().includes('suite') && inp.offsetParent !== null
            );
            if (refocus) { refocus.focus(); console.log('↪️ Re-focused input.'); }
            return true;
        } catch (err) {
            console.error('❌ Error:', err.message);
            return false;
        }
    }

    async function checkForPendingUnits() {
        if (state.isProcessing) return;
        try {
            const res = await fetch(`${SERVER_URL}/valet/pending`, { headers: headers() });
            if (!res.ok) return;
            const data = await res.json();
            if (data.status === 'empty' || data.units.length === 0) return;

            console.log(`\n📋 ${data.count} pending unit(s)`);
            const unitData = data.units[0];
            const unit = unitData.unit;

            if (state.processedUnits.has(unit)) {
                console.log(`⏭️  Skipping ${unit} (already done)`);
                await fetch(`${SERVER_URL}/valet/complete`, {
                    method: 'POST',
                    headers: headers(),
                    body: JSON.stringify({ unit, success: true }),
                });
                return;
            }

            console.log(`🔄 Processing: ${unit} (${unitData.name})`);
            state.isProcessing = true;
            const success = await addUnit(unit);
            if (success) {
                state.processedUnits.add(unit);
                await fetch(`${SERVER_URL}/valet/complete`, {
                    method: 'POST',
                    headers: headers(),
                    body: JSON.stringify({ unit, success: true }),
                });
                console.log(`✅ Confirmed: ${unit}`);
            }
            state.isProcessing = false;
        } catch (err) {
            console.error('❌ Polling error:', err.message);
            state.isProcessing = false;
        }
    }

    function startListener() {
        if (state.isRunning) { console.log('⚠️ Already running'); return; }
        const input = [...document.querySelectorAll('input')].find(
            inp => inp.placeholder && inp.placeholder.toLowerCase().includes('suite')
        );
        if (!input) {
            console.error('❌ "ADD DELIVERY" popup not open!');
            console.log('💡 Click "ADD DELIVERY" first, then call startValetListener()');
            return;
        }
        state.isRunning = true;
        console.log('\n' + '='.repeat(60));
        console.log('🚀 1VALET AUTO-LISTENER STARTED');
        console.log('='.repeat(60));
        console.log(`📡 Server: ${SERVER_URL}`);
        console.log(`⏱️  Poll:   every ${CONFIG.pollInterval / 1000}s`);
        console.log('✋ Stop:   stopValetListener()');
        console.log('='.repeat(60) + '\n');
        state.pollTimer = setInterval(checkForPendingUnits, CONFIG.pollInterval);
        checkForPendingUnits();
    }

    function stopListener() {
        if (!state.isRunning) { console.log('⚠️ Not running'); return; }
        clearInterval(state.pollTimer);
        state.isRunning = false;
        console.log('\n🛑 Listener stopped');
    }

    function checkStatus() {
        console.log('\n📊 STATUS\n' + '='.repeat(60));
        console.log(`Running:    ${state.isRunning ? '✅ Yes' : '❌ No'}`);
        console.log(`Processing: ${state.isProcessing ? '✅ Yes' : '❌ No'}`);
        console.log(`Processed:  ${[...state.processedUnits].join(', ') || 'none'}`);
        console.log(`Server:     ${SERVER_URL}`);
        console.log('='.repeat(60) + '\n');
    }

    function clearCache() { state.processedUnits.clear(); console.log('✅ Cache cleared'); }

    window.startValetListener = startListener;
    window.stopValetListener  = stopListener;
    window.valetStatus        = checkStatus;
    window.valetClearCache    = clearCache;
    window.addUnit            = addUnit;

    console.log(`
╔══════════════════════════════════════════════════════════╗
║  1VALET AUTO-LISTENER  —  injected by ParcelVision       ║
╚══════════════════════════════════════════════════════════╝
  Server : ${SERVER_URL}
  Start  : startValetListener()
  Stop   : stopValetListener()
  Status : valetStatus()
`);

    if (CONFIG.autoStart) {
        console.log('🔄 Auto-starting in 3s...\n');
        setTimeout(startListener, 3000);
    }
})();
