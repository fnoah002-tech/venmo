exports.handler = async function (event) {
  try {
    const q = event.queryStringParameters || {};

    const clickId = q.click_id || "";
    const payout = Number(q.payout || 5.30);

    const eventId =
      q.txid ||
      (clickId ? `reco_${clickId}` : `reco_${Date.now()}`);

    const payload = {
      company_id: "biz_O5LDZ6SDymAiyD",
      event_name: "complete_registration",
      action_source: "website",
      currency: "usd",
      value: Number.isFinite(payout) && payout > 0 ? payout : 5.30,
      event_id: eventId,

      context: {
        click_id: clickId,

        ad_id: q.ad_id || "",
        adset_id: q.adset_id || "",
        campaign_id: q.meta_campaign_id || "",

        ad_name: q.ad_name || "",
        adset_name: q.adset_name || "",
        campaign_name: q.campaign_name || "",

        source: q.source || "",
        placement: q.placement || "",

        clickflare_campaign_id: q.cf_campaign_id || ""
      }
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

    console.log("ClickFlare conversion received:", {
      clickId,
      payout,
      ad_id: q.ad_id,
      adset_id: q.adset_id,
      meta_campaign_id: q.meta_campaign_id,
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
        whop_response: whopResponse
      })
    };
  } catch (error) {
    console.error("Bridge error:", error);

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
