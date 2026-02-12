# NET4000-Team-15

Team Members:

- Ishani Singh
- Nicki Karimi 
- Quentin Heredia 
- Shawn Rae 

## Project - Delay-Predictive Routing for Emulated Non-Terrestrial Networks 

**Objective:** Building an emulated Non-Terrestrial Network (NTN) and evaluate delay-predictive routing and compare it against routing.

### Initial Set up 

- Create dynamic topology + NTN scenarios 
- Implement routing 
- Collect telemetry 

### AI/ML Implementation 

- Train regression model to predict near-future link/path delay 
- Integrate predicted delay into routing decisions 
- Run experiments 

After completing the following objectives, we can analyze the accuracy of our model compared to network performance trade-offs

---

# Most Recent Update

Initial approach involved using mininet with ODL controller to implement network with appropriate script to trace paths.

However:
- While everything worked in base, was very difficult to identify telemetry data and couldn't work with mininet to involve adding delay, jitter or any other testing circumstances while the topology was live and not causing L2 loops 

So our solution was the following:
- Utilize linux network namespaces
- Use OSPF and FRR containers to create the routers and connections to ensure a dynamic topology that can be changed on the fly to create any test scenarios needed.

From here, we can now define different testing environments and begin to collect telemetry data and train our AI model as well as implement simulation of satellites to sync up with new topology set up.
