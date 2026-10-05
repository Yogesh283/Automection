<?php

header("Content-Type: application/json; charset=utf-8");
header("Access-Control-Allow-Origin: *");
header("Access-Control-Allow-Methods: POST, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type");

if ($_SERVER["REQUEST_METHOD"] === "OPTIONS") {
    http_response_code(204);
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

unset($data["password"], $data["mobileNumber"]);

function to_int($value, $default = 0)
{
    if ($value === null || $value === "") {
        return $default;
    }

    return intval($value);
}

$save = [
    "startingAmount" => strval(to_int($data["startingAmount"] ?? 0)),
    "maxLevels" => strval(to_int($data["maxLevels"] ?? 1, 1)),
    "levelAmounts" => is_array($data["levelAmounts"] ?? null) ? $data["levelAmounts"] : [],
    "choice" => strval($data["choice"] ?? ""),
    "stopLoss" => strval(to_int($data["stopLoss"] ?? 0)),
    "targetProfit" => strval(to_int($data["targetProfit"] ?? 0)),
];

$json_file = __DIR__ . "/frontend-settings.json";
$json_ok = file_put_contents(
    $json_file,
    json_encode($save, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE)
) !== false;

$db_ok = false;
$db_error = "";
$config_file = __DIR__ . "/config.php";

if (is_file($config_file)) {
    $config = require $config_file;

    try {
        $dsn = sprintf(
            "mysql:host=%s;dbname=%s;charset=utf8mb4",
            $config["host"],
            $config["database"]
        );
        $db = new PDO($dsn, $config["user"], $config["password"], [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        ]);

        $db->exec(
            "CREATE TABLE IF NOT EXISTS `settings` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `mobile_number` VARCHAR(20),
                `starting_amount` INT,
                `max_levels` INT,
                `level_amounts` TEXT,
                `choice` VARCHAR(20),
                `stop_loss` INT,
                `target_profit` INT,
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"
        );

        $level_amounts = json_encode($save["levelAmounts"], JSON_UNESCAPED_UNICODE);
        $existing = $db->query("SELECT `id` FROM `settings` ORDER BY `id` DESC LIMIT 1");
        $row = $existing->fetch(PDO::FETCH_ASSOC);

        if ($row) {
            $statement = $db->prepare(
                "UPDATE `settings`
                 SET `starting_amount` = ?,
                     `max_levels` = ?,
                     `level_amounts` = ?,
                     `choice` = ?,
                     `stop_loss` = ?,
                     `target_profit` = ?
                 WHERE `id` = ?"
            );
            $statement->execute([
                to_int($save["startingAmount"]),
                to_int($save["maxLevels"], 1),
                $level_amounts,
                $save["choice"],
                to_int($save["stopLoss"]),
                to_int($save["targetProfit"]),
                $row["id"],
            ]);
        } else {
            $statement = $db->prepare(
                "INSERT INTO `settings` (
                    `starting_amount`,
                    `max_levels`,
                    `level_amounts`,
                    `choice`,
                    `stop_loss`,
                    `target_profit`
                ) VALUES (?, ?, ?, ?, ?, ?)"
            );
            $statement->execute([
                to_int($save["startingAmount"]),
                to_int($save["maxLevels"], 1),
                $level_amounts,
                $save["choice"],
                to_int($save["stopLoss"]),
                to_int($save["targetProfit"]),
            ]);
        }

        $db_ok = true;
    } catch (Exception $error) {
        $db_error = $error->getMessage();
    }
}

if ($db_ok) {
    echo json_encode([
        "success" => true,
        "message" => "Settings hosting पर save हो गई।",
        "data" => $save,
    ]);
    exit;
}

if ($json_ok) {
    $message = is_file($config_file)
        ? "Settings file में save हो गई। MySQL connect नहीं हुआ।"
        : "Settings hosting पर save हो गई।";

    echo json_encode([
        "success" => true,
        "message" => $message,
        "data" => $save,
    ]);
    exit;
}

echo json_encode([
    "success" => false,
    "error" => $db_error !== "" ? "Database में save नहीं हुआ।" : "Hosting पर save नहीं हुआ।",
]);
