import express from "express";

const app = express();
const router = express.Router();

app.get("/status", (req, res) => res.send("ok"));
router.post("/items", (req, res) => res.send("created"));
router.get("/items/{id}", (req, res) => res.send(req.params.id));

app.use("/api", router);

app.get(`/dyn/${"x"}`, (req, res) => res.send("dyn"));
