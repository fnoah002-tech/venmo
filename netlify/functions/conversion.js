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

    const payload = {
      company_id: "biz_O5LDZ6SDymAiyD",
      event_name: "complete_registration",
      action_source: "website",
      currency: "usd",
      value: value,
      event_id: eventId
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
