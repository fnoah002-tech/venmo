exports.handler = async function () {
  try {
    const eventId = `test_${Date.now()}`;

    const payload = {
      company_id: "biz_O5LDZ6SDymAiyD",
      event_name: "complete_registration",
      action_source: "website",
      currency: "usd",
      value: 5.30,
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

    const result = await response.text();

    return {
      statusCode: response.status,
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        success: response.ok,
        status: response.status,
        whop_response: result
      })
    };
  } catch (error) {
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
