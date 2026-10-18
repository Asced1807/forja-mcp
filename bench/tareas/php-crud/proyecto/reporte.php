<?php
require __DIR__ . '/datos.php';

$items = productos();
echo "Productos: " . count($items) . "\n";
echo "Total: " . number_format(total($items), 2) . "\n";
