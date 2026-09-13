exports.handler = async function (event) {
  try {
    const q = event.queryStringParameters || {};
    const clickId = q.click_id || "";
    const txid = q.txid || "";
    const payout = Number(q.payout);

    if (!Number.isFinite(payout) || payout <= 0) {
      return { statusCode: 204, body: "" };
    }

    const valid = (value) =>
      value && !value.includes("{") && !value.includes("}");

    const context = {};
    if (valid(q.campaign_id)) context.ad_campaign_id = q.campaign_id;
    if (valid(q.adset_id)) context.ad_set_id = q.adset_id;
    if (valid(q.ad_id)) context.ad_id = q.ad_id;
    if (valid(q.fbclid)) context.fbclid = q.fbclid;

    const eventId = txid ? `reco_${txid}` : `reco_${clickId}`;

    const payload = {
      account_id: "biz_O5LDZ6SDymAiyD",
      event_name: "complete_registration",
      action_source: "website",
      currency: "usd",
      value: payout,
      event_id: eventId,
      context
    };

    const response = await fetch("https://api.whop.com/api/v1/events", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.WHOP_API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    const whopResponse = await response.text();

    console.log("ClickFlare -> Whop", {
      click_id: clickId,
      txid,
      value: payout,
      event_id: eventId,
      context,
      whop_status: response.status
    });

    return {
      statusCode: response.ok ? 200 : 502,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        success: response.ok,
        whop_status: response.status,
        whop_response: whopResponse
      })
    };
  } catch (error) {
    return {
      statusCode: 500,
      body: JSON.stringify({
        success: false,
        error: error.message
      })
    };
  }
};
