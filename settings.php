<?php

header("Content-Type: application/json; charset=utf-8");
header("Access-Control-Allow-Origin: *");
header("Access-Control-Allow-Methods: GET, POST, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type");

if ($_SERVER["REQUEST_METHOD"] === "OPTIONS") {
    http_response_code(204);
    exit;
}

$json_file = __DIR__ . "/frontend-settings.json";

if ($_SERVER["REQUEST_METHOD"] === "GET") {
    if (is_file($json_file)) {
        echo file_get_contents($json_file);
        exit;
    }
    echo json_encode(new stdClass());
    exit;
}

if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    echo json_encode([
        "success" => false,
        "error" => "Sirf POST allowed hai.",
    ]);
    exit;
}

$raw = file_get_contents("php://input");
$data = json_decode($raw, true);

if (!is_array($data)) {
    echo json_encode([
        "success" => false,
        "error" => "Invalid JSON.",
    ]);
    exit;
}

function to_int($value, $default = 0)
{
    if ($value === null || $value === "") {
        return $default;
    }
    return intval($value);
}

$mobile_number = trim(strval($data["mobileNumber"] ?? ""));
unset($data["password"]);

$save = [
    "startingAmount" => strval(to_int($data["startingAmount"] ?? 0)),
    "maxLevels" => strval(to_int($data["maxLevels"] ?? 1, 1)),
    "levelAmounts" => is_array($data["levelAmounts"] ?? null) ? $data["levelAmounts"] : [],
    "choice" => strval($data["choice"] ?? ""),
    "stopLoss" => strval(to_int($data["stopLoss"] ?? 0)),
    "targetProfit" => strval(to_int($data["targetProfit"] ?? 0)),
    "startBot" => true,
    "requestedAt" => gmdate("c"),
];

if ($mobile_number !== "") {
    $save["mobileNumber"] = $mobile_number;
}

$json_ok = file_put_contents(
    $json_file,
    json_encode($save, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE)
) !== false;

if (!$json_ok) {
    echo json_encode([
        "success" => false,
        "error" => "Hosting पर save नहीं हुआ।",
    ]);
    exit;
}

echo json_encode([
    "success" => true,
    "started" => true,
    "message" => "Settings save हो गई। PC पर watch_hosting.py चालू हो तो bot start होगा।",
    "data" => $save,
]);
