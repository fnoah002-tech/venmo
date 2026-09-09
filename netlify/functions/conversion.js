exports.handler = async function (event) {
  try {
    const q = event.queryStringParameters || {};

    const clickId = q.click_id || "";
    const payout = Number(q.payout);

    const value =
      Number.isFinite(payout) && payout > 0
        ? payout
        : 5.30;

    const eventId = clickId
      ? `reco_${clickId}`
      : `reco_${Date.now()}`;

    // Meta attribution data coming from ClickFlare
    const context = {};

    if (q.campaign_id) {
      context.ad_campaign_id = q.campaign_id;
    }

    if (q.adset_id) {
      context.ad_set_id = q.adset_id;
    }

    if (q.ad_id) {
      context.ad_id = q.ad_id;
    }

    if (q.fbclid) {
      context.fbclid = q.fbclid;
    }

    const payload = {
      company_id: "biz_O5LDZ6SDymAiyD",
      event_name: "complete_registration",
      action_source: "website",
      currency: "usd",
      value: value,
      event_id: eventId,
      context: context
    };

    const response = await fetch(
      "https://api.whop.com/api/v1/conversions",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${process.env.WHOP_API_KEY}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      }
    );

    const whopResponse = await response.text();

    console.log("ClickFlare -> Whop", {
      click_id: clickId,
      value,
      event_id: eventId,
      context,
      whop_status: response.status
    });

    return {
      statusCode: response.ok ? 200 : 502,
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        success: response.ok,
        whop_status: response.status,
        whop_response: whopResponse,
        context: context
      })
    };

  } catch (error) {
    console.error(error);

    return {
      statusCode: 500,
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        success: false,
        error: error.message
      })
    };
  }
};
