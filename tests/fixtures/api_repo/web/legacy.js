const app = express();

app.get("/legacy", (req, res) => res.send("old"));

fetch("/v1/pets");
